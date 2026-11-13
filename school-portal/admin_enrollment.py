"""Reviewed enrollment decisions and audited historical tuition opening credits."""
import calendar,hashlib,json,secrets
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo
from flask import abort,flash,g,redirect,render_template,request,session,url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


def amount(value):
    try:result=Decimal(str(value))
    except (InvalidOperation,TypeError):raise ValueError('Enter a valid monetary amount.')
    if not result.is_finite() or result<0 or result>Decimal('99999999.99') or result!=result.quantize(Decimal('.01')):raise ValueError('Use a nonnegative amount with at most two decimal places.')
    return result.quantize(Decimal('.01'))


def installments(balance,size,start,frequency):
    if balance<=0:return []
    if size<=0:raise ValueError('Enter a positive installment amount.')
    if frequency not in ('monthly','weekly','fortnightly'):raise ValueError('Choose a payment frequency.')
    result=[];remaining=balance
    while remaining>0:
        if len(result)>=120:raise ValueError('Use no more than 120 installments.')
        i=len(result)
        if frequency=='monthly':
            month=start.month-1+i;year=start.year+month//12;month=month%12+1
            due=date(year,month,min(start.day,calendar.monthrange(year,month)[1]))
        else:due=start+timedelta(days=i*(7 if frequency=='weekly' else 14))
        value=min(size,remaining);remaining-=value
        result.append(dict(label=f'Agreed installment {i+1}',amount=str(value),due=due.isoformat(),timing='Agreed individual tuition schedule.'))
    return result


def define(db,now):
    class EnrollmentReview(db.Model):
        __tablename__='portal_enrollment_reviews'
        enrollment_id=db.Column(db.Integer,db.ForeignKey('portal_enrollments.id'),primary_key=True)
        status=db.Column(db.String(20),nullable=False,default='pending')
        student_kind=db.Column(db.String(12),nullable=False)
        agreed_total=db.Column(db.Numeric(12,2),nullable=False)
        currency=db.Column(db.String(3),nullable=False)
        revision=db.Column(db.Integer,nullable=False,default=1)
        reason=db.Column(db.Text,nullable=False,default='')
        updated_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class EnrollmentOpeningCredit(db.Model):
        __tablename__='portal_enrollment_opening_credits'
        enrollment_id=db.Column(db.Integer,db.ForeignKey('portal_enrollments.id'),primary_key=True)
        amount=db.Column(db.Numeric(12,2),nullable=False)
        currency=db.Column(db.String(3),nullable=False)
        recorded_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class EnrollmentReconciliationAudit(db.Model):
        __tablename__='portal_enrollment_reconciliation_audit'
        id=db.Column(db.Integer,primary_key=True)
        student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False)
        enrollment_id=db.Column(db.Integer,db.ForeignKey('portal_enrollments.id'),nullable=False)
        action=db.Column(db.String(20),nullable=False)
        snapshot=db.Column(db.JSON,nullable=False)
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    return dict(EnrollmentReview=EnrollmentReview,EnrollmentOpeningCredit=EnrollmentOpeningCredit,EnrollmentReconciliationAudit=EnrollmentReconciliationAudit)


def install(app,db,m,h):
    Review,Credit,Audit=(m[x] for x in ('EnrollmentReview','EnrollmentOpeningCredit','EnrollmentReconciliationAudit'))
    Enrollment,Plan,Invoice=(m[x] for x in ('Enrollment','EnrollmentPaymentSchedule','FeeInvoice'))
    service=app.extensions['integrations'];now,aware=m['now'],m['aware']
    def credit(enrollment,currency):
        row=db.session.get(Credit,enrollment.id)
        return row.amount if row and row.currency==currency else Decimal(0)
    def approved(enrollment):
        row=db.session.get(Review,enrollment.id)
        return not row or row.status=='approved'
    def preview(student,enrollment=None):
        entries=list(db.session.scalars(select(m['PaymentEntry']).where(m['PaymentEntry'].enrollment_id==enrollment.id))) if enrollment else []
        plan=db.session.get(Plan,enrollment.id) if enrollment else None
        selection=db.session.get(m['BillingSelection'],enrollment.id) if enrollment else None
        course=enrollment.course if enrollment else None
        policy=db.session.get(m['CoursePaymentPolicy'],course.id) if course else None
        price=(db.session.get(m['GlobalTuition'],course.id) or db.session.get(m['CoursePrice'],(course.id,student.country)) or db.session.get(m['CoursePrice'],(course.id,'Other'))) if course else None
        review=db.session.get(Review,enrollment.id) if enrollment else None
        total=plan.total if plan else selection.total if selection else review.agreed_total if review else policy.total if policy and policy.enabled else price.total if price else Decimal(0)
        currency=plan.currency if plan else selection.currency if selection else review.currency if review else policy.currency if policy and policy.enabled else price.currency if price else 'USD'
        paid=sum((entry.amount for entry in entries if entry.currency==currency),Decimal(0))+(credit(enrollment,currency) if enrollment else 0)
        promotion=db.session.get(m['BonanzaEnrollment'],enrollment.id) if enrollment else None
        state=dict(promotion={'program':promotion.program,'level':promotion.level,'months':promotion.months,'monthly':str(promotion.monthly_usd),'start':str(promotion.start_date),'confirmed':str(promotion.confirmed_at)} if promotion else None,enrollment=enrollment.id if enrollment else None,course=course.id if course else None,cohort=enrollment.cohort_id if enrollment else None,status=enrollment.status if enrollment else None,payment=enrollment.payment_status if enrollment else None,payment_reference=enrollment.payment_reference if enrollment else None,total=str(total),currency=currency,paid=str(paid),kind=student.kind,review_revision=review.revision if review else 0,entries=[(e.id,str(e.amount),e.currency) for e in entries],stages=plan.stages if plan else None)
        version=hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()
        return dict(total=total,currency=currency,paid=paid,balance=max(Decimal(0),total-paid),version=version,review=review,state=state,plan=plan)
    def pending_funds(enrollment):
        if enrollment.payment_status=='submitted':return True
        if db.session.scalar(select(m['LocalCheckout'].id).join(m['LocalPaymentQuote'],m['LocalPaymentQuote'].id==m['LocalCheckout'].quote_id).where(m['LocalPaymentQuote'].enrollment_id==enrollment.id,m['LocalPaymentQuote'].credited.is_(False)).limit(1)):return True
        if db.session.scalar(select(m['PlanPaymentProof'].id).where(m['PlanPaymentProof'].enrollment_id==enrollment.id,m['PlanPaymentProof'].status=='submitted').limit(1)):return True
        invoice=db.session.scalar(select(Invoice).where(Invoice.enrollment_id==enrollment.id))
        return bool(invoice and any(db.session.scalar(select(m[name].id).where(m[name].invoice_id==invoice.id,m[name].paid.is_(False)).limit(1)) for name in ('CardCheckout','FlutterwaveCheckout')))
    @app.route('/admin/students/<student_id>/enrollment-review',methods=['GET','POST'])
    @h['admin_required']
    def admin_enrollment_review(student_id):
        student=db.get_or_404(m['Student'],student_id)
        eid=request.values.get('enrollment_id',type=int)
        enrollment=db.session.scalar(select(Enrollment).where(Enrollment.id==eid).with_for_update()) if eid else None
        if enrollment and enrollment.student_id!=student.id:abort(403)
        if eid and not enrollment:abort(404)
        if db.session.get(m['StudentArchive'],student.id):abort(400,'Restore the student record before reviewing enrollment.')
        data=preview(student,enrollment)
        if request.method=='POST':
            service['reauthenticate']()
            try:
                if request.form.get('version')!=data['version']:raise ValueError('The enrollment or payment record changed. Reload and review the latest balance.')
                decision=request.form.get('decision','save');mode=request.form.get('mode','confirm');reason=request.form.get('reason','').strip()[:2000]
                if decision not in ('approve','decline','save') or mode not in ('confirm','custom'):raise ValueError('Choose a valid enrollment decision.')
                if not reason:raise ValueError('Record the reason and the source of the verified tuition information.')
                kind=request.form.get('student_kind')
                if kind not in ('new','existing'):raise ValueError('Confirm whether this is a new or existing student.')
                if decision=='decline':
                    if not enrollment:raise ValueError('Choose an existing enrollment request to decline.')
                    course=enrollment.course;total=data['total'];currency=data['currency'];paid=data['paid']
                else:
                    course=db.get_or_404(m['Course'],request.form.get('course_id',type=int))
                    if not course.publication or not course.publication.published:raise ValueError('Choose a published program.')
                    if enrollment and enrollment.course_id!=course.id:raise ValueError('Create a separate enrollment when assigning a different program; existing payment records remain attached to their course.')
                    if request.form.get('confirm_program')!='yes' or request.form.get('confirm_tuition')!='yes':raise ValueError('Confirm the program and agreed tuition before approving or saving.')
                    total=amount(request.form.get('agreed_total'));currency=request.form.get('currency','')
                    if currency not in m['CURRENCIES']:raise ValueError('Choose a supported agreement currency.')
                    cohort=None
                    if request.form.get('cohort_id') and request.form.get('existing_start'):raise ValueError('Choose either a listed cohort or a historical start date, not both.')
                    if request.form.get('cohort_id'):
                        cohort=db.get_or_404(m['Cohort'],request.form.get('cohort_id',type=int))
                        if cohort.course_id!=course.id:raise ValueError('The cohort must belong to the selected program.')
                    if not cohort and request.form.get('existing_start'):
                        if kind!='existing':raise ValueError('Historical cohort dates are available for existing students.')
                        start=date.fromisoformat(request.form['existing_start'])
                        if start>now().astimezone(ZoneInfo('America/Toronto')).date():raise ValueError('Use a listed cohort for a future intake.')
                        cohort=db.session.scalar(select(m['Cohort']).where(m['Cohort'].course_id==course.id,m['Cohort'].start_date==start))
                        if not cohort:cohort=m['Cohort'](course_id=course.id,start_date=start,label='Verified existing cohort');db.session.add(cohort);db.session.flush()
                    if not cohort:raise ValueError('Select a program cohort or enter an existing cohort start date.')
                    if not enrollment:
                        if db.session.scalar(select(Enrollment.id).where(Enrollment.student_id==student.id,Enrollment.course_id==course.id)):raise ValueError('This student already has an enrollment for that program. Open its review instead.')
                        enrollment=Enrollment(student_id=student.id,course_id=course.id,cohort_id=cohort.id);db.session.add(enrollment);db.session.flush()
                    if enrollment.payment_status=='submitted' and enrollment.payment_reference and request.form.get('verify_reference')=='yes' and request.form.get('records_confirmed')=='yes':
                        enrollment.payment_status='verified';enrollment.verified_at=enrollment.verified_at or now()
                    if mode=='confirm':
                        if not data['state']['enrollment'] or total!=data['total'] or currency!=data['currency']:raise ValueError('Use an individual tuition agreement to set or change financial terms.')
                        if enrollment.cohort_id and cohort.id!=enrollment.cohort_id:raise ValueError('Use the individual agreement review to change this cohort.')
                        enrollment.cohort_id=cohort.id
                        paid=data['paid']
                        if decision=='approve' and request.form.get('allow_access')=='yes' and enrollment.payment_status!='verified':
                            if enrollment.payment_status=='submitted' and enrollment.payment_reference and request.form.get('verify_reference')=='yes' and request.form.get('records_confirmed')=='yes':
                                enrollment.payment_status='verified';enrollment.verified_at=enrollment.verified_at or now()
                            else:raise ValueError('Verify the enrollment payment before approving access, or reconcile an existing tuition agreement.')
                    else:
                        if pending_funds(enrollment):raise ValueError('Reconcile the outstanding payment or checkout before replacing tuition terms.')
                        if (data['plan'] or db.session.get(m['BillingSelection'],enrollment.id) or db.session.get(m['BonanzaEnrollment'],enrollment.id)) and request.form.get('replace_terms')!='yes':raise ValueError('Explicitly confirm replacing the existing individual agreement.')
                        if total==0 and request.form.get('tuition_waiver')!='yes':raise ValueError('Explicitly confirm a zero-tuition waiver and record its reason.')
                        paid=amount(request.form.get('paid_total','0'))
                        entries=list(db.session.scalars(select(m['PaymentEntry']).where(m['PaymentEntry'].enrollment_id==enrollment.id)))
                        if any(entry.currency!=currency for entry in entries):raise ValueError('Existing payments use another currency. Reconcile currencies separately before changing this agreement.')
                        online=sum((entry.amount for entry in entries),Decimal(0))
                        if paid>online and request.form.get('records_confirmed')!='yes':raise ValueError('Verify the historical payment record before applying opening credit.')
                        if paid<online:raise ValueError('Paid-to-date cannot be lower than payments already recorded. Use a separately audited refund or correction.')
                        if paid>total:raise ValueError('Paid-to-date exceeds agreed tuition. Reconcile the overpayment before saving.')
                        stages=[]
                        if paid:stages.append(dict(label='Verified tuition paid to date',amount=str(paid),due=now().astimezone(ZoneInfo('America/Toronto')).date().isoformat(),timing='Historical opening credit and existing verified portal payments; not a new cash receipt.'))
                        if total>paid:
                            due=date.fromisoformat(request.form.get('next_due',''));stages+=installments(total-paid,amount(request.form.get('installment_amount')),due,request.form.get('frequency','monthly'))
                        if not stages:stages=[dict(label='Tuition waived by agreement',amount='0.00',due=cohort.start_date.isoformat(),timing='Recorded tuition waiver.')]
                        stages[0]['first_class']=cohort.start_date.isoformat()
                        old_selection=db.session.get(m['BillingSelection'],enrollment.id)
                        if old_selection:db.session.delete(old_selection)
                        plan=db.session.get(Plan,enrollment.id) or Plan(enrollment_id=enrollment.id)
                        plan.total=total;plan.currency=currency;plan.checkout_url='';plan.stages=stages;db.session.add(plan)
                        opening=db.session.get(Credit,enrollment.id) or Credit(enrollment_id=enrollment.id)
                        opening.amount=paid-online;opening.currency=currency;db.session.add(opening)
                        enrollment.cohort_id=cohort.id
                        invoice=db.session.scalar(select(Invoice).where(Invoice.enrollment_id==enrollment.id))
                        if invoice:invoice.status='existing_access' if paid or total==0 else 'pending'
                        else:db.session.add(Invoice(enrollment_id=enrollment.id,amount_usd=Decimal(0),status='existing_access'))
                review=db.session.get(Review,enrollment.id) or Review(enrollment_id=enrollment.id,revision=0)
                review.status={'approve':'approved','decline':'declined','save':'pending'}[decision];review.student_kind=kind;review.agreed_total=total;review.currency=currency;review.reason=reason;review.revision+=1;review.updated_at=now();db.session.add(review)
                student.kind=kind
                db.session.flush()
                if decision=='decline':
                    enrollment.status='rejected';app.extensions['student_management']['hold'](enrollment,'Enrollment declined: '+reason)
                    enrollment.status='rejected'
                elif decision=='approve':
                    control=db.session.get(m['StudentControl'],student.id)
                    if control and not control.active:raise ValueError('Reactivate the student account before approving enrollment.')
                    if request.form.get('allow_access')=='yes':
                        if request.form.get('records_confirmed')!='yes':raise ValueError('Confirm the verified tuition record before activating program access.')
                        app.extensions['student_management']['change'](enrollment,'completed' if (paid>=total and (total>0 or mode=='custom')) else 'current',reason,source='admin')
                    else:enrollment.status='pending'
                else:enrollment.status='pending'
                db.session.add(Audit(student_id=student.id,enrollment_id=enrollment.id,action=decision,snapshot={'before':data['state'],'after':preview(student,enrollment)['state'],'reason':reason,'reviewer':app.extensions['member_profiles']['audit_actor']()}))
                db.session.add(m['StudentManagementAudit'](student_id=student.id,enrollment_id=enrollment.id,action='enrollment_'+decision,detail=f'{course.name}; {kind}; {currency} {total}; paid {paid}; balance {total-paid}. '+reason[:500]))
                notification_kind='enrollment_approved' if decision=='approve' and enrollment.status=='approved' and data['state']['status']!='approved' else 'enrollment_review'
                student_note=request.form.get('student_message','').strip()[:1000]
                money=app.extensions['local_experience']['money']
                summary=f'Program: {course.name}\nStudent status: {kind}\nDecision: {review.status}\n'
                if decision!='decline':summary+='Agreed tuition: '+money(total,currency,student)+'\nVerified paid to date: '+money(paid,currency,student)+'\nOutstanding balance: '+money(total-paid,currency,student)+'\n'
                summary+='Learning access: '+('Available with an active account.' if enrollment.status=='approved' else 'Pending approval/payment requirements.' if decision!='decline' else 'Not approved for this program.')+'\n'
                summary+=student_note or 'Review your enrollment in the portal or contact support for the next steps.'
                app.extensions['notifications']['enqueue'](student,notification_kind,payload={'subject':'LionTech enrollment '+review.status,'body':summary})
                db.session.commit();flash('Enrollment decision and individual tuition agreement saved. Historical credit is separate from new cash receipts.')
                return redirect(url_for('admin',view='students',student=student.id,tab='payments'))
            except (ValueError,IntegrityError) as error:
                db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'Another enrollment update was saved. Reload before continuing.');enrollment=db.session.get(Enrollment,eid) if eid else None;data=preview(student,enrollment)
        courses=list(db.session.scalars(select(m['Course']).join(m['CoursePublication']).where(m['CoursePublication'].published.is_(True))))
        m['upcoming_cohorts']()
        return render_template('admin_enrollment_review.html',student=student,enrollment=enrollment,data=data,courses=courses,cohorts=list(db.session.scalars(select(m['Cohort']).order_by(m['Cohort'].start_date.desc()))))
    @app.context_processor
    def context():return dict(enrollment_opening_credit=credit,enrollment_review_data=preview,enrollment_decision=lambda enrollment:db.session.get(Review,enrollment.id))
    app.extensions['admin_enrollment']=dict(preview=preview,credit=credit,approved=approved)
