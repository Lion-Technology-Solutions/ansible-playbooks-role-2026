"""Audited student operations and tuition-based access, with recoverable deletion."""
import re
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from flask import abort,flash,g,redirect,render_template,request,session,url_for
from sqlalchemy import select,text,event,inspect
from sqlalchemy.orm import Session
from flask import current_app,has_app_context
from experience import installment_schedule
from providers import ORIGIN


@event.listens_for(Session,'before_flush')
def guard_tuition_access(session_,context,instances):
    if not has_app_context() or 'student_management' not in current_app.extensions:return
    current_app.extensions['student_management']['guard'](session_)


def install(app,db,m,h):
    Student,Enrollment,Tuition,Control,Archive,Audit=(m[x] for x in ('Student','Enrollment','TuitionControl','StudentControl','StudentArchive','StudentManagementAudit'))
    now,aware=m['now'],m['aware'];notifications=app.extensions['notifications'];service=app.extensions['integrations']
    def audit(student,action,enrollment=None,detail=''):
        db.session.add(Audit(student_id=student.id,enrollment_id=enrollment.id if enrollment else None,action=action,detail=detail[:1000]))
    def tuition(enrollment):return db.session.get(Tuition,enrollment.id)
    def status(enrollment):
        row=tuition(enrollment)
        return row.status if row else ('Unrecorded' if enrollment.payment_status=='verified' else 'pending')
    def active(student):
        row=db.session.get(Control,student.id)
        return not db.session.get(Archive,student.id) and (not row or row.active)
    def whatsapp(student):
        phone=re.sub(r'[\s()\-]','',student.phone)
        if not re.fullmatch(r'\+[1-9][0-9]{6,14}',phone):return ''
        return 'https://wa.me/'+phone[1:]+'?'+urlencode({'text':f'Hello {student.name}, this is LionTech Academy regarding your enrollment. Student number: {student.student_number}. Please review your portal: {ORIGIN}/enrollments'})
    def change(enrollment,new_status,reason,source='admin',grant=True):
        if new_status not in ('pending','current','completed','overdue'):abort(400)
        row=tuition(enrollment)
        created=row is None
        if not row:row=Tuition(enrollment_id=enrollment.id,status='pending',revision=0,access_hold=False);db.session.add(row)
        if not created and row.status==new_status and not row.access_hold and (new_status not in ('current','completed') or enrollment.status=='approved'):return row
        db.session.info.setdefault('tuition_managed',set()).add(enrollment.id)
        row.status=new_status;row.reason=reason;row.source=source;row.revision+=1;row.updated_at=now()
        if source=='admin':row.access_hold=False
        if new_status in ('current','completed'):
            if grant and not row.access_hold and active(enrollment.student):
                # Administrative reconciliation verifies eligibility, without fabricating cash receipts.
                enrollment.payment_status='verified';enrollment.verified_at=enrollment.verified_at or now()
                if not enrollment.payment_reference:enrollment.payment_reference='ADMIN-RECONCILED-'+str(enrollment.id)
                h['confirm_enrollment'](enrollment)
                invoice=db.session.scalar(select(m['FeeInvoice']).where(m['FeeInvoice'].enrollment_id==enrollment.id))
                if invoice and invoice.status=='pending':invoice.status='existing_access'
        else:enrollment.status='revoked'
        key=f'tuition:{enrollment.id}:{row.revision}'
        payload={'enrollment_id':enrollment.id,'course':enrollment.course.name,'status':new_status,'reason':reason,'tuition_revision':row.revision}
        notifications['enqueue'](enrollment.student,'tuition_status',key+':email',payload)
        notifications['enqueue'](enrollment.student,'tuition_status',key+':whatsapp',payload,channel='whatsapp')
        h['notify'](enrollment.student_id,'Tuition status updated',f'{enrollment.course.name}: {new_status}. '+('Class access is available when your account is active.' if new_status in ('current','completed') else 'Course access is paused. Review your payment options or contact support.'))
        audit(enrollment.student,'tuition_'+new_status,enrollment,source+': '+reason)
        return row
    def hold(enrollment,reason='Manual access revocation'):
        row=tuition(enrollment)
        if not row:row=Tuition(enrollment_id=enrollment.id,status='pending',revision=0);db.session.add(row)
        row.access_hold=True;row.reason=reason;row.revision=(row.revision or 0)+1;row.updated_at=now();enrollment.status='revoked'
    def guard(target):
        for enrollment in list(target.dirty):
            if not isinstance(enrollment,Enrollment) or enrollment.status!='approved':continue
            row=target.get(Tuition,enrollment.id)
            if row and (row.access_hold or row.status in ('pending','overdue')):enrollment.status='revoked'
    def sweep():
        if db.engine.dialect.name=='postgresql':db.session.execute(text('SELECT pg_advisory_xact_lock(73592048)'))
        changed=0
        plans=list(db.session.scalars(select(m['BillingSelection'])).all())+list(db.session.scalars(select(m['EnrollmentPaymentSchedule'])).all())
        for plan in plans:
            enrollment=db.session.scalar(select(Enrollment).where(Enrollment.id==plan.enrollment_id).with_for_update())
            row=tuition(enrollment)
            if not active(enrollment.student) or enrollment.status=='rejected' or (row and (row.access_hold or (row.status=='completed' and row.source=='admin'))):continue
            if enrollment.status!='approved' and (not row or row.status!='overdue'):continue
            if enrollment.payment_status!='verified':continue
            pref=db.session.get(m['StudentPreference'],enrollment.student_id)
            today=now().astimezone(ZoneInfo(pref.timezone if pref else 'America/Toronto')).date()
            if isinstance(plan,m['EnrollmentPaymentSchedule']):
                today=now().astimezone(ZoneInfo('America/Toronto')).date()
                state=app.extensions['program_link_plans']['billing'](enrollment)
                paid=state['collected']
                past_due=sum((part['amount'] for part in state['parts'] if part['due'] and part['due']<today),Decimal(0))
            else:
                paid=sum((entry.amount for entry in db.session.scalars(select(m['PaymentEntry']).where(m['PaymentEntry'].enrollment_id==enrollment.id,m['PaymentEntry'].currency==plan.currency,~m['PaymentEntry'].id.in_(select(m['FeeLedger'].entry_id))))),Decimal(0))
                past_due=sum((part['amount'] for part in installment_schedule(plan.total,plan.installments,plan.starts_on) if part['due']<today),Decimal(0))
            new_status='completed' if paid>=plan.total else ('overdue' if paid<past_due else 'current')
            if not row or row.status!=new_status:
                change(enrollment,new_status,'Automatic tuition-plan reconciliation based on recorded payments.',source='plan');changed+=1
        db.session.commit();return changed

    @app.context_processor
    def context():
        return dict(tuition_status=status,tuition_record=tuition,student_active=active,student_archived=lambda student:bool(db.session.get(Archive,student.id)),student_whatsapp=whatsapp,student_management_audit=lambda student:list(db.session.scalars(select(Audit).where(Audit.student_id==student.id).order_by(Audit.created_at.desc()).limit(20))) if session.get('admin') else [])

    @app.post('/admin/students/<student_id>/manage')
    @h['admin_required']
    def student_manage(student_id):
        service['reauthenticate']()
        student=db.get_or_404(Student,student_id)
        action=request.form.get('action','');reason=request.form.get('reason','').strip()[:500]
        enrollment=None
        if request.form.get('enrollment_id'):
            enrollment=db.session.scalar(select(Enrollment).where(Enrollment.id==request.form.get('enrollment_id',type=int)).with_for_update())
            if not enrollment or enrollment.student_id!=student.id:abort(403)
        archived=db.session.get(Archive,student.id)
        if archived and action not in ('restore',):abort(400,'Restore this deleted account before changing it.')
        if action in ('tuition','approve_existing'):
            if not enrollment:abort(400,'Choose a program enrollment.')
            new_status='current' if action=='approve_existing' else request.form.get('tuition_status')
            if new_status in ('current','completed') and (request.form.get('records_confirmed')!='yes' or not reason):abort(400,'Confirm the existing tuition record and enter a reconciliation note before granting access.')
            if new_status in ('current','completed') and not active(student):abort(400,'Reactivate the student account before granting access.')
            change(enrollment,new_status,reason or 'Administrative tuition update.')
        elif action=='approve':
            control=db.session.get(Control,student.id) or Control(student_id=student.id,active=True,revision=0)
            if not control.active:abort(400,'Reactivate the account first.')
            control.reviewed_at=control.reviewed_at or now();db.session.add(control);audit(student,'approve_account')
        elif action in ('suspend','reactivate','delete','restore'):
            control=db.session.get(Control,student.id) or Control(student_id=student.id,active=True,revision=0)
            if action=='delete':
                if request.form.get('confirm_number')!=student.student_number:abort(400,'Enter the student number to confirm recoverable deletion.')
                db.session.add(Archive(student_id=student.id,reason=reason))
            if action=='restore' and archived:db.session.delete(archived)
            control.active=action=='reactivate';control.revision+=1;control.reason=reason or action;db.session.add(control)
            # Restoring a deleted record leaves it suspended for deliberate review.
            db.session.query(m['AuthSession']).filter_by(student_id=student.id).delete(synchronize_session=False)
            audit(student,action,detail=reason)
        elif action=='revoke':
            if not enrollment:abort(400,'Choose a program enrollment.')
            hold(enrollment,reason or 'Access revoked by administrator.');audit(student,'revoke_access',enrollment,reason)
        elif action=='email':
            subject=request.form.get('subject','').strip()[:160];body=request.form.get('body','').strip()[:10000]
            if not subject or not body:abort(400,'Enter a subject and message.')
            notifications['enqueue'](student,'admin_message',payload={'subject':subject,'body':body});audit(student,'email_requested',detail=subject)
        elif action=='whatsapp_notice':
            if not enrollment:abort(400,'Choose a program enrollment.')
            row=tuition(enrollment)
            if not row:abort(400,'Record the tuition status first.')
            notifications['enqueue'](student,'tuition_status',payload={'enrollment_id':enrollment.id,'course':enrollment.course.name,'status':row.status,'reason':row.reason,'tuition_revision':row.revision},channel='whatsapp');audit(student,'whatsapp_requested',enrollment)
        else:abort(400)
        db.session.commit();flash('Student update saved. Applicable email and WhatsApp notifications are sent directly to configured providers; student consent applies. Check notification activity for delivery results.')
        return redirect(url_for('admin',view='students',archived='yes' if action in ('delete',) else None))

    return dict(change=change,hold=hold,sweep=sweep,guard=guard)
