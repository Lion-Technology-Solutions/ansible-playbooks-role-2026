"""Thirty-day, individually warned, recoverable deletion for unresolved tuition."""
import hashlib,json,secrets
from datetime import date,datetime,timedelta,time,timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from flask import abort,flash,g,render_template,request,url_for,redirect
from sqlalchemy import select,text

ZONE=ZoneInfo('America/Toronto')


def define(db,now):
    class PaymentRetentionPolicy(db.Model):
        __tablename__='portal_payment_retention_policy'
        id=db.Column(db.Integer,primary_key=True,default=1)
        activated_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
        enabled=db.Column(db.Boolean,nullable=False,default=True)
    class PaymentRetentionPolicyAudit(db.Model):
        __tablename__='portal_payment_retention_policy_audit'
        id=db.Column(db.Integer,primary_key=True)
        enabled=db.Column(db.Boolean,nullable=False)
        reason=db.Column(db.Text,nullable=False)
        actor=db.Column(db.String(100),nullable=False,default='admin:1')
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class PaymentDeletionCase(db.Model):
        __tablename__='portal_payment_deletion_cases'
        student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),primary_key=True)
        generation=db.Column(db.String(32),nullable=False,default=lambda:secrets.token_hex(16))
        fingerprint=db.Column(db.String(64),nullable=False)
        status=db.Column(db.String(20),nullable=False,default='warning')
        started_at=db.Column(db.DateTime(timezone=True),nullable=False)
        deadline_at=db.Column(db.DateTime(timezone=True),nullable=False)
        enrollment_ids=db.Column(db.JSON,nullable=False)
        reason=db.Column(db.String(500),nullable=False,default='')
        pause_until=db.Column(db.DateTime(timezone=True))
        deleted_at=db.Column(db.DateTime(timezone=True))
    return dict(PaymentRetentionPolicy=PaymentRetentionPolicy,PaymentDeletionCase=PaymentDeletionCase,PaymentRetentionPolicyAudit=PaymentRetentionPolicyAudit)


def install(app,db,m,h):
    Case,Policy,Student,Enrollment,Event=(m[x] for x in ('PaymentDeletionCase','PaymentRetentionPolicy','Student','Enrollment','LifecycleEvent'))
    aware=m['aware'];notifications=app.extensions['notifications']
    def clock():return app.config.get('RETENTION_CLOCK',m['now'])()
    def day():return aware(clock()).astimezone(ZONE).date()
    def financial(enrollment):
        tuition=db.session.get(m['TuitionControl'],enrollment.id)
        if enrollment.status=='rejected' or (tuition and tuition.access_hold):return None,'manual_review'
        if tuition and tuition.status=='completed' and tuition.source=='admin':return None,'paid'
        fixed=db.session.get(m['EnrollmentPaymentSchedule'],enrollment.id)
        if fixed:
            paid=sum(db.session.scalars(select(m['PaymentEntry'].amount).where(m['PaymentEntry'].enrollment_id==enrollment.id,m['PaymentEntry'].currency==fixed.currency)),Decimal(0))
            paid+=app.extensions['admin_enrollment']['credit'](enrollment,fixed.currency)
            remaining=max(Decimal(0),fixed.total-paid)
            if remaining<=0:return None,'paid'
            parts=[];cumulative=Decimal(0);arrears=Decimal(0)
            try:
                for part in fixed.stages:
                    cumulative+=Decimal(part['amount'])
                    due=date.fromisoformat(part['due']) if part.get('due') else None
                    if cumulative>paid and due and due<=day():parts.append(due);arrears+=min(Decimal(part['amount']),cumulative-paid)
            except (ValueError,KeyError):return None,'manual_review'
            if not parts:return None,'current'
            return dict(enrollment_id=enrollment.id,due=min(parts).isoformat(),due_amount=str(arrears),balance=str(remaining),currency=fixed.currency),'debt'
        plan=db.session.get(m['BillingSelection'],enrollment.id)
        if plan:
            from experience import installment_schedule
            paid=sum(db.session.scalars(select(m['PaymentEntry'].amount).where(m['PaymentEntry'].enrollment_id==enrollment.id,m['PaymentEntry'].currency==plan.currency,~m['PaymentEntry'].id.in_(select(m['FeeLedger'].entry_id)))),Decimal(0))
            if paid>=plan.total:return None,'paid'
            cumulative=Decimal(0);first=None;arrears=Decimal(0)
            for part in installment_schedule(plan.total,plan.installments,plan.starts_on):
                cumulative+=part['amount']
                if cumulative>paid and part['due']<=day():first=first or part['due'];arrears+=min(part['amount'],cumulative-paid)
            if not first:return None,'current'
            return dict(enrollment_id=enrollment.id,due=first.isoformat(),due_amount=str(arrears),balance=str(plan.total-paid),currency=plan.currency),'debt'
        invoice=db.session.scalar(select(m['FeeInvoice']).where(m['FeeInvoice'].enrollment_id==enrollment.id))
        if not fixed and not plan and db.session.scalar(select(m['PaymentEntry'].id).where(m['PaymentEntry'].enrollment_id==enrollment.id).limit(1)):return None,'manual_review'
        if invoice and invoice.status=='pending' and invoice.amount_usd>0 and enrollment.payment_status!='verified':
            return dict(enrollment_id=enrollment.id,due=aware(invoice.created_at).astimezone(ZONE).date().isoformat(),balance=str(invoice.amount_usd),due_amount=str(invoice.amount_usd),currency='USD'),'debt'
        # No amount or due date is not evidence of delinquency.
        return None,'manual_review' if enrollment.status!='rejected' else 'excluded'
    def evidence(student):
        control=db.session.get(m['StudentControl'],student.id)
        protected='Manually suspended account requires review.' if control and not control.active else ''
        membership=db.session.get(m['ProfileMembership'],'student:'+student.id)
        if membership and db.session.scalar(select(m['ProfileMembership'].actor).where(m['ProfileMembership'].person_id==membership.person_id,m['ProfileMembership'].kind!='student').limit(1)):protected='Linked staff or administrator profile.'
        if db.session.scalar(select(m['StaffMember'].id).where(db.func.lower(m['StaffMember'].email)==student.email.lower()).limit(1)):protected='Staff identity requires manual review.'
        admin=db.session.get(m['AdminAccount'],1)
        if admin and admin.email.lower()==student.email.lower():protected='Administrator identity requires manual review.'
        debts=[]
        enrollments=list(db.session.scalars(select(Enrollment).where(Enrollment.student_id==student.id)))
        for enrollment in enrollments:
            debt,state=financial(enrollment)
            review=db.session.get(m['EnrollmentReview'],enrollment.id)
            if review and review.status=='pending':protected='Enrollment agreement awaiting administrator review.'
            if debt:debts.append(debt)
            if state in ('paid','current','manual_review') and enrollment.status!='rejected':protected=protected or 'Another paid/current program or unresolved access review protects this account.'
            if enrollment.payment_status=='submitted':protected='Payment submitted for verification.'
            if db.session.scalar(select(m['LocalCheckout'].id).join(m['LocalPaymentQuote'],m['LocalPaymentQuote'].id==m['LocalCheckout'].quote_id).where(m['LocalPaymentQuote'].enrollment_id==enrollment.id,m['LocalCheckout'].paid.is_(True),m['LocalPaymentQuote'].credited.is_(False)).limit(1)):protected='Provider payment awaiting reconciliation.'
            if db.session.scalar(select(m['PlanPaymentProof'].id).where(m['PlanPaymentProof'].enrollment_id==enrollment.id,m['PlanPaymentProof'].status=='submitted').limit(1)):protected='Payment evidence awaiting review.'
        debts.sort(key=lambda d:d['enrollment_id'])
        return debts,protected
    def fingerprint(debts):return hashlib.sha256(json.dumps([(d['enrollment_id'],d['due']) for d in debts]).encode()).hexdigest()
    def valid_warning(event):
        policy=db.session.get(Policy,1)
        if not app.config.get('PAYMENT_RETENTION_ENABLED',True) or (policy and not policy.enabled):return False
        case=db.session.get(Case,event.student_id)
        if not case or case.generation!=event.payload.get('generation') or case.status!='warning' or db.session.get(m['StudentArchive'],event.student_id):return False
        debts,protected=evidence(event.student)
        return bool(debts and not protected and fingerprint(debts)==case.fingerprint and (not case.pause_until or aware(case.pause_until)<=aware(clock())))
    def warning_text(event):
        case=db.session.get(Case,event.student_id);deadline=app.extensions['local_experience']['time'](case.deadline_at,event.student) if case else event.payload.get('deadline','')
        preference=db.session.get(m['StudentPreference'],event.student_id);french=bool(preference and preference.language=='fr')
        debts,_=evidence(event.student)
        amounts=[]
        for debt in debts:
            course=db.session.get(Enrollment,debt['enrollment_id']).course.name
            amounts.append(course+(': montant actuellement dû ' if french else ': amount currently due ')+app.extensions['local_experience']['money'](debt['due_amount'],debt['currency'],event.student)+('; solde restant ' if french else '; total remaining ')+app.extensions['local_experience']['money'](debt['balance'],debt['currency'],event.student))
        if french:
            return 'Avertissement de suppression de votre compte LionTech',('Bonjour '+event.student.name+',\n\nVotre compte présente un paiement en attente ou en retard. Sans régularisation, votre compte sera supprimé de façon réversible le '+deadline+'. La connexion et l’accès aux cours seront désactivés. Vos paiements, justificatifs et dossiers seront conservés pour régularisation.\n\n'+'\n'.join(amounts)+'\n\nConsultez vos paiements : https://www.liontechacademy.com/enrollments\nSi vous avez déjà payé ou avez besoin d’une correction, contactez-nous avant cette date : liontechacademy@gmail.com · https://wa.me/14375756656\nNuméro étudiant : '+event.student.student_number)
        return 'LionTech account deletion warning',('Hello '+event.student.name+',\n\nYour account has an unresolved pending or past-due payment. Your account is scheduled for recoverable deletion on '+deadline+'. Sign-in and learning access will stop. Payment, enrollment and audit records are retained for reconciliation.\n\n'+ '\n'.join(amounts)+'\n\nReview your balance and pay: https://www.liontechacademy.com/enrollments\nIf you already paid or need a correction, contact support before the deadline: liontechacademy@gmail.com · https://wa.me/14375756656\nStudent number: '+event.student.student_number)
    def sweep():
        if not app.config.get('PAYMENT_RETENTION_ENABLED',True):return dict(warned=0,deleted=0,held=0)
        if db.engine.dialect.name=='postgresql':db.session.execute(text('SELECT pg_advisory_xact_lock(73592131)'))
        policy=db.session.scalar(select(Policy).where(Policy.id==1).with_for_update().execution_options(populate_existing=True))
        if not policy:policy=Policy(id=1,activated_at=clock());db.session.add(policy);db.session.flush()
        counts=dict(warned=0,deleted=0,held=0)
        if not policy.enabled:return counts
        for student in db.session.scalars(select(Student).where(~Student.id.in_(select(m['StudentArchive'].student_id)))).all():
            # Use the same enrollment locks as provider settlement; financial state is re-read after locking.
            records=db.session.scalars(select(Enrollment).where(Enrollment.student_id==student.id).with_for_update().execution_options(populate_existing=True)).all()
            db.session.scalar(select(Student).where(Student.id==student.id).with_for_update())
            debts,protected=evidence(student);case=db.session.scalar(select(Case).where(Case.student_id==student.id).with_for_update().execution_options(populate_existing=True))
            if not debts:
                if case and case.status!='deleted':case.status='resolved';case.reason='Automatic deletion no longer applies: the payment or account status has changed.'
                continue
            key=fingerprint(debts)
            if not case:case=Case(student_id=student.id,fingerprint=key,started_at=clock(),deadline_at=clock()+timedelta(days=30),enrollment_ids=[d['enrollment_id'] for d in debts]);db.session.add(case);db.session.flush()
            if case.fingerprint!=key or case.status in ('resolved','deleted'):
                case.generation=secrets.token_hex(16);case.fingerprint=key;case.started_at=clock();case.deadline_at=clock()+timedelta(days=30);case.enrollment_ids=[d['enrollment_id'] for d in debts];case.status='warning'
            if protected or (case.pause_until and aware(case.pause_until)>aware(clock())):
                case.status='held';case.reason=protected or 'Administrator extension.';counts['held']+=1;continue
            if case.status=='held':
                case.status='warning';case.generation=secrets.token_hex(16);case.started_at=clock();case.deadline_at=clock()+timedelta(days=30)
            case.reason='Unresolved payment; daily warning and final financial recheck required.'
            accepted=db.session.scalar(select(Event).where(Event.student_id==student.id,Event.kind=='payment_deletion_warning',Event.status=='accepted',Event.payload['generation'].as_string()==case.generation).order_by(Event.accepted_at).limit(1))
            if accepted and accepted.accepted_at:case.deadline_at=max(aware(case.deadline_at),aware(accepted.accepted_at)+timedelta(days=7))
            if not accepted and aware(clock())>=aware(case.deadline_at):case.deadline_at=clock()+timedelta(days=7);case.reason='Deletion held: no accepted warning email. A new delivery grace period applies.'
            if aware(clock())>=aware(case.deadline_at) and accepted and accepted.accepted_at and aware(clock())>=aware(accepted.accepted_at)+timedelta(days=7):
                # No hard delete: preserve agreements, receipts, submitted work and the audit trail.
                control=db.session.get(m['StudentControl'],student.id) or m['StudentControl'](student_id=student.id,active=False,revision=0)
                control.active=False;control.revision+=1;control.reason='Automatic recoverable deletion: payment unresolved after 30-day warning window.';db.session.add(control)
                db.session.add(m['StudentArchive'](student_id=student.id,reason=control.reason,deleted_at=clock()))
                db.session.query(m['AuthSession']).filter_by(student_id=student.id).delete(synchronize_session=False)
                case.status='deleted';case.deleted_at=clock()
                db.session.add(m['StudentManagementAudit'](student_id=student.id,action='automatic_payment_deletion',detail='Case '+case.generation+'; deadline '+aware(case.deadline_at).isoformat()+'; warning accepted '+aware(accepted.accepted_at).isoformat()))
                notifications['enqueue'](student,'admin_message',key='deletion-complete:'+case.generation,payload={'subject':'LionTech account recoverably deleted','body':'Your account was recoverably deleted after the unresolved payment warning period. Contact support to reconcile payment and request restoration. Financial records have been retained.'})
                counts['deleted']+=1
            else:
                # A failed notification provider never silently advances deletion.
                if aware(clock())>=aware(case.deadline_at) and not accepted:case.reason='Deletion held: no accepted warning email. Delivery must be restored first.'
                event_key='payment-delete:'+case.generation+':'+day().isoformat()
                if not db.session.scalar(select(Event.id).where(Event.event_key==event_key)):
                    notifications['enqueue'](student,'payment_deletion_warning',event_key,{'generation':case.generation,'deadline':aware(case.deadline_at).isoformat()});counts['warned']+=1
        db.session.commit();return counts
    @app.get('/admin/payment-deletion')
    @h['admin_required']
    def payment_deletion_admin():
        return render_template('payment_deletion_admin.html',cases=list(db.session.scalars(select(Case).order_by(Case.deadline_at))),students={s.id:s for s in db.session.scalars(select(Student))},retention_now=clock(),policy=db.session.get(Policy,1))
    @app.post('/admin/payment-deletion/policy')
    @h['admin_required']
    def payment_deletion_policy():
        app.extensions['integrations']['reauthenticate']()
        reason=request.form.get('reason','').strip()[:1000]
        if not reason:abort(400,'Record the reason for changing the deletion policy.')
        enabled=request.form.get('enabled')=='yes';policy=db.session.scalar(select(Policy).where(Policy.id==1).with_for_update()) or Policy(id=1,activated_at=clock())
        policy.enabled=enabled;db.session.add(policy)
        for case in db.session.scalars(select(Case).where(Case.status.in_(['warning','held']))):case.status='held';case.reason='Policy changed: '+reason[:450]
        db.session.add(m['PaymentRetentionPolicyAudit'](enabled=enabled,reason=reason,actor=app.extensions['member_profiles']['audit_actor']()));db.session.commit();flash('Automatic deletion policy updated. Resuming starts a fresh warning window for held cases.')
        return redirect(url_for('payment_deletion_admin'))
    @app.context_processor
    def context():
        student=getattr(g,'student',None);case=db.session.get(Case,student.id) if student else None
        if case:
            from types import SimpleNamespace
            if not valid_warning(SimpleNamespace(student=student,student_id=student.id,payload={'generation':case.generation})):case=None
        return dict(payment_deletion_notice=case)
    @app.post('/admin/payment-deletion/<student_id>/extend')
    @h['admin_required']
    def payment_deletion_extend(student_id):
        app.extensions['integrations']['reauthenticate']()
        db.session.scalars(select(Enrollment).where(Enrollment.student_id==student_id).with_for_update()).all()
        db.session.scalar(select(Student).where(Student.id==student_id).with_for_update())
        case=db.session.scalar(select(Case).where(Case.student_id==student_id).with_for_update().execution_options(populate_existing=True))
        if not case:abort(404)
        reason=request.form.get('reason','').strip()[:500]
        if not reason:abort(400,'Record the reason for the extension.')
        case.deadline_at=max(aware(case.deadline_at),aware(clock()))+timedelta(days=30);case.reason=reason
        db.session.add(m['StudentManagementAudit'](student_id=student_id,action='payment_deletion_extended',detail=reason));db.session.commit();flash('Deletion deadline extended by 30 days. Daily warnings continue until payment is reconciled.')
        return redirect(url_for('payment_deletion_admin'))
    app.extensions['account_retention']=dict(sweep=sweep,valid_warning=valid_warning,warning_text=warning_text,evidence=evidence,clock=clock)
