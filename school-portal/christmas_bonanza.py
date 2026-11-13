"""Christmas Bonanza: server-priced, enrollment-bound offers and monthly intakes."""
import calendar
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from flask import abort,flash,g,redirect,render_template,request,session,url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

ZONE=ZoneInfo('America/Toronto')
START=datetime(2026,11,13,tzinfo=ZONE)
END=datetime(2027,3,1,tzinfo=ZONE)
MONTHLY=Decimal('67.00')
PROGRAMS={'devops':('DevOps',6),'cloud-computing':('Cloud Computing',3),'cybersecurity':('Cybersecurity',2)}


def family(slug):
    return next((name for name in PROGRAMS if slug==name or slug.startswith(name+'-')),None)


def fourth_saturdays(today):
    result=[]
    for year in range(max(2027,today.year),max(2027,today.year)+2):
        for month in range(1,13):
            saturdays=[week[5] for week in calendar.monthcalendar(year,month) if week[5]]
            value=date(year,month,saturdays[3])
            if value>=today:result.append(value)
    return result


def add_months(start,offset):
    month=start.month-1+offset;year=start.year+month//12;month=month%12+1
    return date(year,month,min(start.day,calendar.monthrange(year,month)[1]))


def payment_stages(start,months,enrolled_on):
    return [dict(label='Month 1 tuition — enrollment included' if i==0 else f'Month {i+1} tuition',amount='67.00',due=(enrolled_on if i==0 else add_months(start,i)).isoformat(),first_class=start.isoformat(),timing='Pay now to confirm enrollment; covers month one.' if i==0 else 'Due on the monthly anniversary of your cohort start date.') for i in range(months)]


def define(db,now):
    class BonanzaEnrollment(db.Model):
        __tablename__='portal_bonanza_enrollments'
        enrollment_id=db.Column(db.Integer,db.ForeignKey('portal_enrollments.id'),primary_key=True)
        program=db.Column(db.String(30),nullable=False)
        level=db.Column(db.String(20),nullable=False)
        start_date=db.Column(db.Date,nullable=False)
        months=db.Column(db.Integer,nullable=False)
        monthly_usd=db.Column(db.Numeric(12,2),nullable=False,default=MONTHLY)
        original_total=db.Column(db.Numeric(12,2))
        expires_at=db.Column(db.DateTime(timezone=True),nullable=False)
        confirmed_at=db.Column(db.DateTime(timezone=True))
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    return dict(BonanzaEnrollment=BonanzaEnrollment)


def install(app,db,m):
    from pathways import PROGRAMS as PATHWAYS
    Binding,Enrollment,Plan,Invoice=(m[k] for k in ('BonanzaEnrollment','Enrollment','EnrollmentPaymentSchedule','FeeInvoice'))
    def clock():return app.config.get('BONANZA_CLOCK',m['now'])()
    def active():return app.config.get('BONANZA_ENABLED',True) and START<=m['aware'](clock())<END
    def stages_for(program):return PATHWAYS[program][1]
    def course_for(program,level):
        if program not in PROGRAMS:raise ValueError('Choose DevOps, Cloud Computing or Cybersecurity.')
        stage=next((s for s in stages_for(program) if s['key']==level),None)
        if not stage:raise ValueError('Choose Foundation, Intermediate or Advanced.')
        course=db.session.scalar(select(m['Course']).where(m['Course'].slug==stage['slug']))
        if not course or not course.publication or not course.publication.published:raise ValueError('This course is not currently accepting enrollments.')
        return course,stage
    def original(course):
        policy=db.session.get(m['CoursePaymentPolicy'],course.id);price=db.session.get(m['GlobalTuition'],course.id)
        return policy.total if policy and policy.enabled and policy.currency=='USD' else price.total if price and price.currency=='USD' else None
    def choice(program,level,day):
        if not active():raise ValueError('The Christmas Bonanza enrollment offer has ended or is not active. No higher-price checkout has been started.')
        course,stage=course_for(program,level)
        try:start=date.fromisoformat(day)
        except (ValueError,TypeError):raise ValueError('Select a highlighted cohort date.')
        if start not in fourth_saturdays(m['aware'](clock()).astimezone(ZONE).date()):raise ValueError('Choose an upcoming fourth-Saturday intake beginning January 2027.')
        return course,stage,start
    def validate_choice(values):return choice(values.get('program'),values.get('level'),values.get('date'))
    def cohort_for(course,start):
        cohort=db.session.scalar(select(m['Cohort']).where(m['Cohort'].course_id==course.id,m['Cohort'].start_date==start))
        if not cohort:cohort=m['Cohort'](course_id=course.id,start_date=start,label='Fourth-Saturday intake');db.session.add(cohort);db.session.flush()
        return cohort
    def mutable(enrollment):
        if enrollment.status in ('approved','rejected','revoked') or enrollment.payment_status in ('verified','submitted'):return False
        if db.session.scalar(select(m['PaymentEntry'].id).where(m['PaymentEntry'].enrollment_id==enrollment.id).limit(1)):return False
        if db.session.get(m['BillingSelection'],enrollment.id):return False
        if db.session.scalar(select(m['LocalCheckout'].id).join(m['LocalPaymentQuote'],m['LocalPaymentQuote'].id==m['LocalCheckout'].quote_id).where(m['LocalPaymentQuote'].enrollment_id==enrollment.id).limit(1)):return False
        if db.session.scalar(select(m['PlanPaymentProof'].id).where(m['PlanPaymentProof'].enrollment_id==enrollment.id).limit(1)):return False
        invoice=db.session.scalar(select(Invoice).where(Invoice.enrollment_id==enrollment.id))
        if invoice:
            if invoice.status!='pending':return False
            for model in (m['CardCheckout'],m['FlutterwaveCheckout'],m['ManualFeePayment']):
                if db.session.scalar(select(model.id).where(model.invoice_id==invoice.id).limit(1)):return False
        return True
    def bind(enrollment,values):
        course,stage,start=validate_choice(values)
        if enrollment.course_id!=course.id:raise ValueError('The offer and selected course do not match.')
        existing=db.session.get(Binding,enrollment.id)
        if existing and existing.confirmed_at and (existing.start_date!=start or existing.level!=stage['key']):raise ValueError('Your confirmed enrollment is preserved. Contact support before changing the cohort or starting level.')
        if existing and (existing.confirmed_at or (existing.start_date==start and existing.level==stage['key'])):return existing
        if not mutable(enrollment):raise ValueError('An existing enrollment, payment or checkout must be reviewed before changing its terms. Your saved payment plan has been preserved.')
        enrollment.cohort_id=cohort_for(course,start).id
        months=PROGRAMS[values['program']][1]
        snapshot=db.session.get(Plan,enrollment.id) or Plan(enrollment_id=enrollment.id,currency='USD',total=MONTHLY*months,checkout_url='',stages=[])
        snapshot.total=MONTHLY*months;snapshot.currency='USD';snapshot.checkout_url='';snapshot.stages=payment_stages(start,months,m['aware'](clock()).astimezone(ZONE).date());db.session.add(snapshot)
        invoice=db.session.scalar(select(Invoice).where(Invoice.enrollment_id==enrollment.id)) or Invoice(enrollment_id=enrollment.id,amount_usd=MONTHLY)
        invoice.amount_usd=MONTHLY;db.session.add(invoice)
        row=existing or Binding(enrollment_id=enrollment.id,program=values['program'],level=stage['key'],start_date=start,months=months,monthly_usd=MONTHLY,original_total=original(course),expires_at=END)
        row.level=stage['key'];row.start_date=start;db.session.add(row)
        db.session.add(m['StudentManagementAudit'](student_id=enrollment.student_id,enrollment_id=enrollment.id,action='bonanza_offer_selected',detail=f'{row.program}; {row.level}; {start}; USD67 x {months}; enrollment credited to month one.'))
        return row
    def resume(student):
        values=session.get('bonanza_choice')
        if not values:return None
        course,stage,start=validate_choice(values)
        enrollment=db.session.scalar(select(Enrollment).where(Enrollment.student_id==student.id,Enrollment.course_id==course.id).with_for_update())
        if not enrollment:
            enrollment=Enrollment(student_id=student.id,course_id=course.id,cohort_id=cohort_for(course,start).id);db.session.add(enrollment);db.session.flush()
        bind(enrollment,values);db.session.commit();return enrollment
    def payment_allowed(enrollment,allow_expired=False):
        row=db.session.get(Binding,enrollment.id)
        if row and not row.confirmed_at and not allow_expired and m['aware'](clock())>=m['aware'](row.expires_at):raise ValueError('The Bonanza enrollment deadline has passed. Contact support to review any payment already made. No higher-price payment has been substituted.')
    def confirm(record,allow_expired=False):
        row=db.session.scalar(select(Binding).where(Binding.enrollment_id==record.quote.enrollment_id).with_for_update())
        if not row or row.confirmed_at:return True
        if record.quote.stage!=0 or record.quote.base_amount!=MONTHLY or record.quote.base_currency!='USD':return False
        if m['aware'](clock())>=m['aware'](row.expires_at) and not allow_expired:return False
        row.confirmed_at=clock();return True
    def overview(program,level):
        course,stage=course_for(program,level);months=PROGRAMS[program][1];total=MONTHLY*months;standard=original(course)
        return dict(course=course,stage=stage,months=months,total=total,standard=standard,saving=standard-total if standard is not None and standard>total else None,monthly=MONTHLY)
    @app.get('/christmas-bonanza')
    def christmas_bonanza():
        program=request.args.get('program','');level=request.args.get('level','foundation');day=request.args.get('date','');review=request.args.get('step')=='review'
        if program and program not in PROGRAMS:abort(404)
        data=None;selected=None
        if program:
            try:
                data=overview(program,level)
                if day:
                    selected=date.fromisoformat(day)
                    if selected not in fourth_saturdays(m['aware'](clock()).astimezone(ZONE).date()):raise ValueError('Choose an available fourth-Saturday intake.')
            except ValueError as error:abort(400,str(error))
        if review and (not data or not selected):abort(400,'Choose a course, date and level before reviewing the offer.')
        dates=fourth_saturdays(m['aware'](clock()).astimezone(ZONE).date())
        anchor=selected or (dates[0] if dates else date(2027,1,23))
        try:year=int(request.args.get('year',anchor.year));month=int(request.args.get('month',anchor.month))
        except ValueError:abort(400)
        if not max(2027,clock().year)<=year<=max(2027,clock().year)+1 or not 1<=month<=12:abort(400)
        grids=[]
        for offset in (0,1):
            first=add_months(date(year,month,1),offset)
            grids.append(dict(year=first.year,month=first.month,label=first.strftime('%B %Y'),weeks=[[date(first.year,first.month,d) if d else None for d in week] for week in calendar.monthcalendar(first.year,first.month)]))
        previous=add_months(date(year,month,1),-1);following=add_months(date(year,month,1),1)
        return render_template('christmas_bonanza.html',program=program,level=level,data=data,selected=selected,review=review,grids=grids,visible_dates={day for grid in grids for week in grid['weeks'] for day in week if day},dates=dates,date_set=set(dates),previous=previous,following=following,programs=PROGRAMS,levels=stages_for(program) if program else [],plan_preview=payment_stages(selected,data['months'],m['aware'](clock()).astimezone(ZONE).date()) if selected else [],offer_active=active())
    @app.post('/christmas-bonanza/claim')
    def bonanza_claim():
        if session.get('admin') or g.staff:abort(403,'Open your Student profile to enroll in your own program.')
        values={key:request.form.get(key,'') for key in ('program','level','date')}
        try:
            if request.form.get('accept_terms')!='yes':raise ValueError('Confirm the displayed enrollment and monthly payment terms.')
            course,stage,start=validate_choice(values);cohort=cohort_for(course,start);db.session.commit()
            session['bonanza_choice']=values;session['application']={'course_id':course.id,'cohort_id':cohort.id}
            if g.student:
                enrollment=resume(g.student);session.pop('bonanza_choice',None);session.pop('application',None);return redirect(url_for('global_payment',enrollment_id=enrollment.id))
            return redirect(url_for('register'))
        except (ValueError,IntegrityError) as error:
            db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'This enrollment was updated in another tab. Please refresh.');return redirect(url_for('christmas_bonanza',program=values['program'] if values['program'] in PROGRAMS else None))
    @app.before_request
    def discovery_entry():
        if request.endpoint=='static':return
        if not active() or session.get('admin') or getattr(g,'staff',None) or getattr(g,'student',None):return
        if request.method=='GET' and request.endpoint in ('program_levels','program_level','level_enroll'):
            program=(request.view_args or {}).get('program')
            if program in PROGRAMS and request.args.get('tab','lessons')=='lessons':return redirect(url_for('christmas_bonanza',program=program,level=(request.view_args or {}).get('level','foundation')))
        if request.method=='GET' and request.endpoint=='application' and request.args.get('program') in PROGRAMS:return redirect(url_for('christmas_bonanza',program=request.args['program']))
    @app.context_processor
    def context():
        return dict(bonanza_active=active(),bonanza_end=END.astimezone(timezone.utc).isoformat(),bonanza_now=m['aware'](clock()).astimezone(timezone.utc).isoformat(),bonanza_monthly=MONTHLY,bonanza_family=family,bonanza_binding=lambda enrollment:db.session.get(Binding,enrollment.id),bonanza_url=lambda program,level='foundation':url_for('christmas_bonanza',program=program,level=level))
    app.extensions['bonanza']=dict(active=active,clock=clock,bind=bind,resume=resume,validate_choice=validate_choice,payment_allowed=payment_allowed,confirm=confirm,overview=overview)
