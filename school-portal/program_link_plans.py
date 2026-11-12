"""Snapshot payment plans for externally hosted checkout with audited receipt review."""
import secrets
import calendar
from datetime import date,timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from flask import abort,flash,g,redirect,render_template,request,session,url_for,Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename
from file_storage import validate_image

PLAN_URL='https://flutterwave.com/pay/liontechpaymentplan'
DEFAULT_STAGES=[{'label':'Enrollment payment — unlocks access','amount':'10.00','timing':'Due immediately on enrollment'}, {'label':'Balance of first payment','amount':'9.99','timing':'Seven days after your first class'}, {'label':'Second payment','amount':'19.99','timing':'One calendar month after the first-payment balance'}, {'label':'Final payment','amount':'20.01','timing':'Two calendar months after the first-payment balance'}]


def dated_stages(stages,start,enrolled_on=None):
    """Calendar dates stay anchored to the first class, never to payment/review time."""
    dates=[None]*4
    if start:
        balance=start+timedelta(days=7)
        dates=[start,balance]
        for offset in (1,2):
            month=balance.month-1+offset;year=balance.year+month//12;month=month%12+1
            dates.append(date(year,month,min(balance.day,calendar.monthrange(year,month)[1])))
    if enrolled_on:dates[0]=enrolled_on
    result=[dict(stage,timing=(stage['timing'] if index==0 and enrolled_on is None else DEFAULT_STAGES[index]['timing']),due=dates[index].isoformat() if dates[index] else None) for index,stage in enumerate(stages)]
    if enrolled_on:result[0]['first_class']=start.isoformat() if start else None
    return result


def snapshot_dates(stages,cohort_start):
    # Version-one plans used the initial due date as their class-date anchor.
    if 'first_class' in stages[0]:
        start=date.fromisoformat(stages[0]['first_class']) if stages[0].get('first_class') else cohort_start
        enrolled_on=date.fromisoformat(stages[0]['due']) if stages[0].get('due') else None
    else:
        start=date.fromisoformat(stages[0]['due']) if stages[0].get('due') else cohort_start
        enrolled_on=None
    return dated_stages(stages,start,enrolled_on)



def uses_default_stages(stages):
    amounts=[part.get('amount') for part in stages]
    return amounts in ([part['amount'] for part in DEFAULT_STAGES], ['10.00','9.99','19.99','19.99'])


def seed_policy(connection,m):
    Course,Policy,Price=(m[x] for x in ('Course','CoursePaymentPolicy','GlobalTuition'))
    course_id=connection.execute(select(Course.id).where(Course.slug=='data-analytics')).scalar()
    if not course_id:return
    policy=connection.execute(select(Policy.__table__).where(Policy.course_id==course_id)).mappings().first()
    if not policy:
        connection.execute(Policy.__table__.insert().values(course_id=course_id,currency='USD',total=Decimal('59.99'),checkout_url=PLAN_URL,stages=DEFAULT_STAGES,enabled=True))
        values=dict(currency='USD',total=Decimal('59.99'),installments=4,full_url=PLAN_URL,installment_url=PLAN_URL)
        table=Price.__table__
        connection.execute(table.update().where(table.c.course_id==course_id).values(**values) if connection.execute(select(table.c.course_id).where(table.c.course_id==course_id)).first() else table.insert().values(course_id=course_id,**values))
    elif uses_default_stages(policy['stages']) and policy['total'] in (Decimal('59.97'),Decimal('59.99')):
        connection.execute(Policy.__table__.update().where(Policy.course_id==course_id).values(total=Decimal('59.99'),stages=DEFAULT_STAGES))
        connection.execute(Price.__table__.update().where(Price.course_id==course_id,Price.total==Decimal('59.97')).values(total=Decimal('59.99')))
    # Fill only the previously unspecified deadlines; preserve amounts, receipts and custom dates.
    Plan,Enrollment,Cohort=(m[x] for x in ('EnrollmentPaymentSchedule','Enrollment','Cohort'))
    rows=connection.execute(select(Plan.__table__,Cohort.start_date.label('cohort_start')).join(Enrollment,Enrollment.id==Plan.enrollment_id).outerjoin(Cohort,Cohort.id==Enrollment.cohort_id).where(Enrollment.course_id==course_id)).mappings().all()
    for row in rows:
        stages=row['stages']
        if not uses_default_stages(stages) or any(part.get('due') for part in stages[1:]):continue
        connection.execute(Plan.__table__.update().where(Plan.enrollment_id==row['enrollment_id']).values(stages=snapshot_dates(stages,row['cohort_start'])))


def install(app,db,m,h):
    Policy,Plan,Proof,Invoice,Entry=(m[x] for x in ('CoursePaymentPolicy','EnrollmentPaymentSchedule','PlanPaymentProof','FeeInvoice','PaymentEntry'))
    def policy(course):
        row=db.session.get(Policy,course.id)
        return row if row and row.enabled else None
    def existing(enrollment):return db.session.get(Plan,enrollment.id)
    def eligible(enrollment):
        if existing(enrollment):return True
        if not policy(enrollment.course) or enrollment.payment_status in ('submitted','verified') or enrollment.status in ('approved','rejected','revoked'):return False
        if db.session.get(m['BillingSelection'],enrollment.id) or db.session.scalar(select(Entry.id).where(Entry.enrollment_id==enrollment.id).limit(1)):return False
        invoice=db.session.scalar(select(Invoice).where(Invoice.enrollment_id==enrollment.id))
        if invoice:
            if invoice.status!='pending':return False
            for model in (m['CardCheckout'],m['FlutterwaveCheckout'],m['ManualFeePayment']):
                if db.session.scalar(select(model.id).where(model.invoice_id==invoice.id).limit(1)):return False
        return True
    def ensure(enrollment):
        snapshot=existing(enrollment)
        if snapshot:
            if uses_default_stages(snapshot.stages) and not any(part.get('due') for part in snapshot.stages[1:]):
                updated=snapshot_dates(snapshot.stages,enrollment.cohort.start_date if enrollment.cohort else None)
                if updated!=snapshot.stages:snapshot.stages=updated;db.session.commit()
            return snapshot
        if not eligible(enrollment):return None
        source=policy(enrollment.course)
        stages=dated_stages(source.stages,enrollment.cohort.start_date if enrollment.cohort else None,m['aware'](enrollment.created_at).astimezone(ZoneInfo('America/Toronto')).date()) if uses_default_stages(source.stages) else source.stages
        if enrollment.course.slug.startswith(('cybersecurity','cloud-computing')):
            from pathways import cyber_payment_stages
            stages=cyber_payment_stages(source.stages,enrollment.cohort.start_date if enrollment.cohort else None)
            choice=session.get('level_payment_choice',{})
            if choice.get('course_id')==enrollment.course_id and choice.get('plan')=='full':
                stages=[dict(label='Full tuition — enrollment included',amount=str(source.total),timing='Due immediately',due=m['academy_today']().isoformat(),first_class=enrollment.cohort.start_date.isoformat() if enrollment.cohort else None)]
        if enrollment.course.slug in ('data-analytics-intermediate','data-analytics-advanced') and len(stages)==1:
            stages=[dict(stages[0],due=m['aware'](enrollment.created_at).astimezone(ZoneInfo('America/Toronto')).date().isoformat(),first_class=enrollment.cohort.start_date.isoformat() if enrollment.cohort else None)]
        snapshot=Plan(enrollment_id=enrollment.id,currency=source.currency,total=source.total,checkout_url=source.checkout_url,stages=stages)
        invoice=db.session.scalar(select(Invoice).where(Invoice.enrollment_id==enrollment.id))
        if not invoice:invoice=Invoice(enrollment_id=enrollment.id,amount_usd=Decimal(stages[0]['amount']));db.session.add(invoice)
        else:invoice.amount_usd=Decimal(stages[0]['amount'])
        db.session.add(snapshot);db.session.commit();return snapshot
    def billing(enrollment):
        plan=ensure(enrollment)
        if not plan:return None
        paid=sum(db.session.scalars(select(Entry.amount).where(Entry.enrollment_id==enrollment.id,Entry.currency==plan.currency)),Decimal(0))
        cumulative=Decimal(0);next_payment=None;parts=[]
        for index,stage in enumerate(plan.stages):
            value=Decimal(stage['amount']);covered=max(Decimal(0),min(value,paid-cumulative));cumulative+=value
            part=dict(stage,index=index,amount=value,remaining=value-covered,paid=covered,due=date.fromisoformat(stage['due']) if stage.get('due') else None)
            parts.append(part)
            if next_payment is None and part['remaining']>0:next_payment=dict(part,amount=part['remaining'])
        terms=SimpleNamespace(enrollment_id=enrollment.id,total=plan.total,currency=plan.currency,installments=len(plan.stages),checkout_url=plan.checkout_url,plan='fixed')
        anchor=plan.stages[0].get('first_class') if 'first_class' in plan.stages[0] else plan.stages[0].get('due')
        return dict(terms=terms,collected=paid,remaining=max(Decimal(0),plan.total-paid),next=next_payment,parts=parts,starts_on=date.fromisoformat(anchor) if anchor else None)
    def owner(enrollment_id):
        enrollment=db.get_or_404(m['Enrollment'],enrollment_id)
        if enrollment.student_id!=g.student.id:abort(403)
        return enrollment
    @app.route('/payments/plans/<int:enrollment_id>',methods=['GET','POST'])
    @h['student_required']()
    def link_payment_plan(enrollment_id):
        # The shared security middleware enforces MFA/email; require a signed-in student here.
        if not g.student: return redirect(url_for('login'))
        enrollment=owner(enrollment_id);plan=ensure(enrollment)
        if not plan:return redirect(url_for('global_payment',enrollment_id=enrollment.id))
        if request.method=='POST':
            try:
                app.extensions['auth_security']['limit']('plan-proof:'+g.student.id,5,60)
                payment_quote=app.extensions['local_payments']['validate'](request.form.get('local_quote_id'),enrollment,expired=True)
                if payment_quote.proof_id or payment_quote.manual_id:raise ValueError('This payment quote has changed or expired. Your receipt is already submitted for review.')
                try:actual_local=Decimal(request.form.get('local_amount',''))
                except Exception:raise ValueError('Enter the local amount actually paid.')
                if not actual_local.is_finite() or actual_local!=payment_quote.amount:raise ValueError('The receipt amount must match your secured local-currency quote. Contact support if you paid a different amount.')
                current=billing(enrollment)['next']
                if not current:raise ValueError('This payment plan is already complete.')
                if not secrets.compare_digest(request.form.get('student_number','').upper().strip(),g.student.student_number):raise ValueError('The student number does not match your signed-in account.')
                if db.session.scalar(select(Proof.id).where(Proof.enrollment_id==enrollment.id,Proof.status=='submitted')):raise ValueError('Your payment proof is already awaiting review. Contact support if you need to correct it.')
                reference=request.form.get('reference','').strip();note=request.form.get('note','').strip()[:1000]
                if not 3<=len(reference)<=150 or any(ord(c)<32 for c in reference):raise ValueError('Enter the payment transaction reference from your receipt.')
                if db.session.scalar(select(Proof.id).where(Proof.reference==reference)) or db.session.scalar(select(m['ManualFeePayment'].id).where(m['ManualFeePayment'].reference==reference)) or db.session.scalar(select(Entry.id).where(Entry.reference==reference)):raise ValueError('That transaction reference is already recorded. Contact support before submitting it again.')
                upload=request.files.get('receipt')
                if not upload or not upload.filename:raise ValueError('Attach your payment receipt as PDF, JPG or PNG, up to 2 MB.')
                data=upload.read(2*1024*1024+1);name=secure_filename(upload.filename)[:180];extension=name.rsplit('.',1)[-1].lower();mime={'pdf':'application/pdf','jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png'}.get(extension)
                if not data or len(data)>2*1024*1024 or not mime:raise ValueError('Choose a PDF, JPG or PNG receipt no larger than 2 MB.')
                if mime=='application/pdf':
                    if not data.startswith(b'%PDF-'):raise ValueError('Upload a valid PDF receipt.')
                else:validate_image(data,mime)
                proof=Proof(enrollment_id=enrollment.id,stage=current['index'],amount=current['amount'],currency=plan.currency,reference=reference,note=note,receipt_name=name,receipt_mime=mime)
                db.session.add(proof);db.session.flush();payment_quote.proof_id=proof.id;proof.receipt_path=app.config['FILE_STORAGE'].put('plan-receipts/'+proof.id+'/'+secrets.token_hex(12),data)
                app.extensions['notifications']['enqueue'](enrollment.student,'support_payment_review',payload={'enrollment_id':enrollment.id,'course':enrollment.course.name,'amount':str(proof.amount)+' USD'})
                h['notify'](enrollment.student_id,'Payment proof submitted','Your payment receipt is awaiting administrator verification. A submission does not grant access.')
                db.session.commit();flash('Payment proof received. Access changes only after an administrator verifies the payment.')
            except (ValueError,IntegrityError) as error:db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'That receipt is already recorded. Contact support.')
            except Exception:db.session.rollback();flash('The receipt could not be saved. Please retry or contact support.')
            return redirect(url_for('link_payment_plan',enrollment_id=enrollment.id))
        return render_template('link_payment_plan.html',e=enrollment,plan=plan,billing=billing(enrollment),proofs=db.session.scalars(select(Proof).where(Proof.enrollment_id==enrollment.id).order_by(Proof.created_at.desc())).all())
    @app.get('/payments/plan-receipts/<proof_id>')
    def plan_receipt(proof_id):
        proof=db.get_or_404(Proof,proof_id)
        if not session.get('admin') and (not g.student or proof.enrollment.student_id!=g.student.id):abort(403)
        return Response(app.config['FILE_STORAGE'].get(proof.receipt_path),mimetype=proof.receipt_mime,headers={'Content-Disposition':'attachment; filename="'+proof.receipt_name+'"'})
    @app.post('/admin/plan-payments/<proof_id>/review')
    @h['admin_required']
    def review_plan_payment(proof_id):
        if not session.get('admin'):return redirect(url_for('admin_login'))
        app.extensions['integrations']['reauthenticate']()
        proof=db.session.scalar(select(Proof).where(Proof.id==proof_id).with_for_update().execution_options(populate_existing=True))
        if not proof:abort(404)
        if proof.status=='approved':return redirect(url_for('admin',view='payments'))
        decision=request.form.get('decision')
        if decision=='more_info':
            feedback=request.form.get('feedback','').strip()[:1000]
            if not feedback:abort(400,'Enter the information needed.')
            proof.status='more_info';proof.feedback=feedback;db.session.commit();flash('The receipt was marked for additional information.')
            return redirect(url_for('admin',view='payments'))
        if decision!='approve' or request.form.get('funds_verified')!='yes':abort(400,'Confirm that you verified the actual Flutterwave payment and USD amount.')
        enrollment=db.session.scalar(select(m['Enrollment']).where(m['Enrollment'].id==proof.enrollment_id).with_for_update().execution_options(populate_existing=True))
        current=billing(enrollment)['next']
        if not current or current['index']!=proof.stage or current['amount']!=proof.amount:abort(409,'The payment plan changed. Review the existing payments before approving this receipt.')
        if db.session.scalar(select(m['ManualFeePayment'].id).where(m['ManualFeePayment'].reference==proof.reference)):abort(409,'This reference is also in enrollment-fee review. Reconcile it before approval.')
        payment_quote=app.extensions['local_payments']['proof_quote'](proof)
        if payment_quote:
            try:app.extensions['local_payments']['validate'](payment_quote.id,enrollment,expired=True)
            except ValueError:abort(409,'The local payment quote no longer matches the outstanding payment. Reconcile before approval.')
            payment_quote.credited=True
        try:
            entry=Entry(enrollment_id=enrollment.id,amount=proof.amount,currency=proof.currency,reference=proof.reference);db.session.add(entry);db.session.flush()
            invoice=db.session.scalar(select(Invoice).where(Invoice.enrollment_id==enrollment.id).with_for_update())
            if proof.stage==0:
                db.session.add(m['FeeLedger'](entry_id=entry.id,invoice_id=invoice.id));invoice.status='paid'
            proof.status='approved';proof.reviewed_at=m['now']()
            state=billing(enrollment);tuition=db.session.get(m['TuitionControl'],enrollment.id)
            grant=enrollment.status!='rejected' and (enrollment.status!='revoked' or bool(tuition and tuition.status=='overdue' and not tuition.access_hold))
            app.extensions['student_management']['change'](enrollment,'completed' if state['remaining']==0 else 'current','Local payment receipt verified: '+proof.reference,source='plan',grant=grant)
            db.session.add(m['StudentManagementAudit'](student_id=enrollment.student_id,enrollment_id=enrollment.id,action='verify_link_payment',detail=proof.currency+' '+str(proof.amount)+' · '+proof.reference))
            db.session.commit();flash('Payment recorded. Enrollment access reflects the verified payment and existing account restrictions.')
        except IntegrityError:db.session.rollback();abort(409,'That transaction has already been recorded.')
        return redirect(url_for('admin',view='payments'))
    def preview(course_id):
        chosen=session.get('application') or {}
        cohort=db.session.get(m['Cohort'],chosen.get('cohort_id')) if chosen.get('cohort_id') else None
        start=cohort.start_date if cohort and cohort.course_id==course_id else None
        source=db.session.get(Policy,course_id)
        if not source:return []
        if db.session.get(m['Course'],course_id).slug.startswith(('cybersecurity','cloud-computing')):
            from pathways import cyber_payment_stages
            parts=cyber_payment_stages(source.stages,start)
            return [dict(part,due=date.fromisoformat(part['due']) if part.get('due') else None,first_class=start) for part in parts]
        return [dict(part,due=date.fromisoformat(part['due']) if part.get('due') else None,first_class=date.fromisoformat(part['first_class']) if part.get('first_class') else None) for part in dated_stages(source.stages,start,m['academy_today']())]
    @app.context_processor
    def link_plan_context():
        return dict(payment_plan_preview=preview,has_link_payment_programs=bool(db.session.scalar(select(Policy.course_id).where(Policy.enabled.is_(True)).limit(1))),program_payment_policy=policy,fixed_plan_for=existing,uses_link_plan=eligible,link_plan_billing=billing,link_plan_proofs=db.session.scalars(select(Proof).order_by(Proof.created_at.desc())).all() if session.get('admin') and request.endpoint=='admin' else [])
    return dict(policy=policy,existing=existing,eligible=eligible,ensure=ensure,billing=billing)
