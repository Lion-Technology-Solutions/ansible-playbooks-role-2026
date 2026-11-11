import io
from datetime import date,timedelta,datetime,timezone
from decimal import Decimal
import pytest
from sqlalchemy import select
from app import db,Enrollment,Course,FeeInvoice,PaymentEntry,FeeLedger,PlanPaymentProof,EnrollmentPaymentSchedule,CoursePaymentPolicy,TuitionControl,BillingSelection
from test_portal import register,admin,analytics,request_enrollment,csrf
from test_lessons_files import MemoryStorage


def setup_plan(app):
    app.config.update(DATA_ANALYTICS_LINK_PLAN=True,FILE_STORAGE=MemoryStorage(),FLUTTERWAVE_HTTP=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No payment API required')))
    student,manager=app.test_client(),app.test_client();register(student,country='Canada');admin(manager)
    course,cohort=analytics(app);request_enrollment(student,course,cohort)
    with app.app_context():eid=db.session.scalar(select(Enrollment.id))
    response=student.get('/payments/enrollment/'+str(eid),follow_redirects=True)
    assert response.status_code==200 and b'Start learning with USD 10.00' in response.data
    return student,manager,eid


def submit(student,eid,reference):
    with student.session_transaction():pass
    import re
    body=student.get('/payments/plans/'+str(eid)).data
    number=re.search(rb'LT-\d{4}-\d+',body)[0].decode()
    return student.post('/payments/plans/'+str(eid),data={'csrf':csrf(student,'/portal'),'student_number':number,'reference':reference,'amount':'0.01','receipt':(io.BytesIO(b'%PDF-1.7\nunit receipt'),'receipt.pdf')},follow_redirects=True)


def review(app,manager,eid):
    with app.app_context():pid=db.session.scalar(select(PlanPaymentProof.id).where(PlanPaymentProof.enrollment_id==eid,PlanPaymentProof.status=='submitted'))
    response=manager.post('/admin/plan-payments/'+pid+'/review',data={'csrf':csrf(manager,'/admin'),'admin_password':'test-admin-password','decision':'approve','funds_verified':'yes'},follow_redirects=True)
    assert response.status_code==200
    return pid


def test_exact_plan_is_published_and_no_extra_enrollment_fee(app):
    student,manager,eid=setup_plan(app)
    with app.app_context():
        plan=db.session.get(EnrollmentPaymentSchedule,eid)
        assert plan.total==Decimal('59.97') and [p['amount'] for p in plan.stages]==['10.00','9.99','19.99','19.99']
        assert all(part['due'] for part in plan.stages)
        assert date.fromisoformat(plan.stages[1]['due'])==date.fromisoformat(plan.stages[0]['due'])+timedelta(days=7)
        assert db.session.scalar(select(FeeInvoice)).amount_usd==Decimal('10.00')
        assert not db.session.get(BillingSelection,eid)
    page=app.test_client().get('/courses/data-analytics').data
    assert b'59.97' in page and b'9.99' in page and b'US$5.00' not in page and b'monthly payments available' not in page
    page=student.get('/payments/plans/'+str(eid)).data
    assert b'https://flutterwave.com/pay/liontechpaymentplan' in page


def test_initial_payment_grants_access_and_full_plan_is_credited_once(app):
    student,manager,eid=setup_plan(app)
    for index,amount in enumerate(['10.00','9.99','19.99','19.99']):
        assert submit(student,eid,'FLW-PLAN-'+str(index)).status_code==200
        with app.app_context():
            proof=db.session.scalar(select(PlanPaymentProof).where(PlanPaymentProof.status=='submitted'))
            assert proof.amount==Decimal(amount)
            if index==0:assert db.session.get(Enrollment,eid).status!='approved'
        pid=review(app,manager,eid)
        # Repeated verification does not record the same funds twice.
        manager.post('/admin/plan-payments/'+pid+'/review',data={'csrf':csrf(manager,'/admin'),'admin_password':'test-admin-password','decision':'approve','funds_verified':'yes'})
        with app.app_context():
            enrollment=db.session.get(Enrollment,eid)
            assert enrollment.status=='approved' and enrollment.payment_status=='verified'
            state=app.extensions['program_link_plans']['billing'](enrollment)
            assert state['collected']==sum(map(Decimal,['10.00','9.99','19.99','19.99'][:index+1]))
            assert state['remaining']==Decimal('59.97')-state['collected']
            if index==0:assert state['next']['amount']==Decimal('9.99') and state['next']['due']==enrollment.cohort.start_date+timedelta(days=7)
    with app.app_context():
        assert len(db.session.scalars(select(PaymentEntry)).all())==4
        assert len(db.session.scalars(select(FeeLedger)).all())==1
        assert db.session.get(TuitionControl,eid).status=='completed'
    assert b'Payment plan complete' in student.get('/payments/plans/'+str(eid)).data


def test_proof_requires_owner_private_receipt_and_explicit_admin_verification(app):
    student,manager,eid=setup_plan(app)
    assert app.test_client().get('/payments/plans/'+str(eid)).status_code==302
    submit(student,eid,'UNIQUE-PLAN-REF')
    with app.app_context():pid=db.session.scalar(select(PlanPaymentProof.id))
    assert app.test_client().get('/payments/plan-receipts/'+pid).status_code==403
    assert student.get('/payments/plan-receipts/'+pid).data.startswith(b'%PDF-')
    bad=manager.post('/admin/plan-payments/'+pid+'/review',data={'csrf':csrf(manager,'/admin'),'admin_password':'test-admin-password','decision':'approve'})
    assert bad.status_code==400
    with app.app_context():assert not db.session.scalar(select(PaymentEntry)) and db.session.get(Enrollment,eid).status!='approved'
    assert b'already awaiting review' in submit(student,eid,'ANOTHER-PLAN-REF').data


def test_manual_revocation_is_preserved_after_verified_plan_payment(app):
    student,manager,eid=setup_plan(app);submit(student,eid,'FIRST-PLAN-REF');review(app,manager,eid)
    with app.app_context():
        app.extensions['student_management']['hold'](db.session.get(Enrollment,eid),'Manual access hold');db.session.commit()
    submit(student,eid,'SECOND-PLAN-REF');review(app,manager,eid)
    with app.app_context():assert db.session.get(Enrollment,eid).status=='revoked' and db.session.get(TuitionControl,eid).access_hold


def test_existing_paid_agreements_are_not_repriced(app):
    app.config.update(FILE_STORAGE=MemoryStorage())
    student,manager=app.test_client(),app.test_client();register(student);admin(manager)
    course,cohort=analytics(app);request_enrollment(student,course,cohort)
    with app.app_context():
        enrollment=db.session.scalar(select(Enrollment));eid=enrollment.id
        db.session.add(BillingSelection(enrollment_id=eid,plan='installments',currency='USD',total=Decimal('120'),installments=3,starts_on=enrollment.cohort.start_date,checkout_url='https://example.com/old-plan'));db.session.commit()
        from program_link_plans import seed_policy
        with db.engine.begin() as connection:
            import app as module
            seed_policy(connection,vars(module))
        assert app.extensions['program_link_plans']['ensure'](enrollment) is None
        assert db.session.get(BillingSelection,eid).total==Decimal('120')
    response=student.post('/enrollment/'+str(eid)+'/plan',data={'csrf':csrf(student,'/portal'),'plan':'installments'},follow_redirects=True)
    assert response.status_code==200 and b'existing payment agreement is preserved' in response.data
    with app.app_context():assert db.session.get(BillingSelection,eid).total==Decimal('120')


def test_deposit_is_not_double_counted_in_forecast_and_access_current_before_due(app):
    from flask import template_rendered
    student,manager,eid=setup_plan(app);seen=[]
    def capture(sender,template,context,**extra):
        if 'overview' in context:seen.append(context['overview'])
    with template_rendered.connected_to(capture,app):manager.get('/admin?currency=USD')
    assert seen[-1]['outstanding']==Decimal('59.97')
    submit(student,eid,'FORECAST-10');review(app,manager,eid)
    with template_rendered.connected_to(capture,app):manager.get('/admin?currency=USD')
    assert seen[-1]['outstanding']==Decimal('49.97') and seen[-1]['collected']==Decimal('10.00')
    with app.app_context():
        app.extensions['student_management']['sweep']()
        assert db.session.get(Enrollment,eid).status=='approved'
        assert db.session.get(TuitionControl,eid).status=='current'


def test_fixed_plan_cannot_start_competing_api_checkout(app):
    student,manager,eid=setup_plan(app)
    with app.app_context():iid=db.session.scalar(select(FeeInvoice.id).where(FeeInvoice.enrollment_id==eid))
    for path in ['/payments/'+iid+'/checkout','/payments/'+iid+'/flutterwave']:
        response=student.post(path,data={'csrf':csrf(student,'/portal')})
        assert response.status_code==302 and response.location.endswith('/payments/plans/'+str(eid))


@pytest.mark.parametrize('start,expected',[
    ('2027-01-14',['2027-01-14','2027-01-21','2027-02-21','2027-03-21']),
    ('2027-01-24',['2027-01-24','2027-01-31','2027-02-28','2027-03-31']),
    ('2028-01-24',['2028-01-24','2028-01-31','2028-02-29','2028-03-31']),
    ('2026-12-25',['2026-12-25','2027-01-01','2027-02-01','2027-03-01']),
])
def test_calendar_schedule_uses_first_class_seven_days_then_calendar_months(start,expected):
    from program_link_plans import dated_stages,DEFAULT_STAGES
    assert [p['due'] for p in dated_stages(DEFAULT_STAGES,date.fromisoformat(start))]==expected


def test_existing_link_plan_dates_backfill_once_without_repricing_or_drifting(app):
    import app as module
    from program_link_plans import seed_policy
    student,manager,eid=setup_plan(app)
    with app.app_context():
        plan=db.session.get(EnrollmentPaymentSchedule,eid)
        original=[dict(part,due='2027-01-14' if index==0 else None) for index,part in enumerate(plan.stages)]
        plan.stages=original;db.session.commit()
        with db.engine.begin() as connection:seed_policy(connection,vars(module))
        db.session.expire_all();plan=db.session.get(EnrollmentPaymentSchedule,eid)
        assert [p['due'] for p in plan.stages]==['2027-01-14','2027-01-21','2027-02-21','2027-03-21']
        enrollment=db.session.get(Enrollment,eid);enrollment.cohort.start_date=date(2030,5,1);db.session.commit()
        state=app.extensions['program_link_plans']['billing'](enrollment)
        assert state['next']['due']==date(2027,1,14) and plan.total==Decimal('59.97')
        # Explicit customized deadlines are retained by repeated startup migrations.
        plan.stages=[dict(p,due='2027-01-25' if i==1 else p['due']) for i,p in enumerate(plan.stages)];db.session.commit()
        with db.engine.begin() as connection:seed_policy(connection,vars(module))
        db.session.expire_all();assert db.session.get(EnrollmentPaymentSchedule,eid).stages[1]['due']=='2027-01-25'
    page=student.get('/payments/plans/'+str(eid)).data
    assert b'January 14, 2027' in page and b'Due January 25, 2027' in page


def test_each_student_has_individual_dates_and_signup_previews_selected_start(app):
    from app import Cohort
    student,manager,eid=setup_plan(app)
    second=app.test_client();register(second,email='another@example.com')
    with app.app_context():
        enrollment=db.session.get(Enrollment,eid);cid=enrollment.course_id
        from app import next_start_dates
        other_cohort=db.session.scalar(select(Cohort).where(Cohort.course_id==cid,Cohort.start_date==next_start_dates()[1]));cohort_id=other_cohort.id
        first_dates=list(db.session.get(EnrollmentPaymentSchedule,eid).stages)
    request_enrollment(second,cid,cohort_id)
    with app.app_context():
        other=db.session.scalar(select(Enrollment).where(Enrollment.id!=eid));other_id=other.id
    second.get('/payments/plans/'+str(other_id))
    with app.app_context():
        other_plan=db.session.get(EnrollmentPaymentSchedule,other_id)
        assert other_plan.stages[0]['due']!=first_dates[0]['due']
        expected=[date.fromisoformat(p['due']).strftime('%B %d, %Y').encode() for p in other_plan.stages]
    visitor=app.test_client()
    visitor.post('/apply/choose',data={'csrf':csrf(visitor,'/apply'),'course_id':cid,'cohort_id':cohort_id})
    page=visitor.get('/register').data
    assert all(d in page for d in expected)
    # The dashboard uses the same individual next deadline, even before paying.
    assert expected[0] in second.get('/payments/plans/'+str(other_id)).data


def test_unknown_start_waits_for_assignment_and_then_gets_dates(app):
    student,manager,eid=setup_plan(app)
    with app.app_context():
        enrollment=db.session.get(Enrollment,eid);enrollment.cohort=None
        plan=db.session.get(EnrollmentPaymentSchedule,eid)
        plan.stages=[dict(p,due=None) for p in plan.stages];db.session.commit()
        assert all(part['due'] is None for part in app.extensions['program_link_plans']['billing'](enrollment)['parts'])
        from app import Cohort
        enrollment.cohort=db.session.scalar(select(Cohort).where(Cohort.course_id==enrollment.course_id));db.session.commit()
        assert all(part['due'] for part in app.extensions['program_link_plans']['billing'](enrollment)['parts'])


def test_due_reminders_and_access_use_staged_dates_and_credit_the_deposit(app):
    from app import LifecycleEvent
    from program_link_plans import dated_stages
    student,manager,eid=setup_plan(app);submit(student,eid,'DATE-DEPOSIT');review(app,manager,eid)
    with app.app_context():
        today=datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('America/Toronto')).date()
        plan=db.session.get(EnrollmentPaymentSchedule,eid)
        plan.stages=dated_stages(plan.stages,today-timedelta(days=7));db.session.commit()
        app.extensions['notifications']['due_reminders']()
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.event_key.like('link-due:%')))
        assert event.payload['amount']=='9.99 USD' and event.payload['due']==today.isoformat()
        assert db.session.get(TuitionControl,eid).status=='current' # Due today, not yet overdue.
        app.extensions['notifications']['due_reminders']()
        assert len(db.session.scalars(select(LifecycleEvent).where(LifecycleEvent.event_key.like('link-due:%'))).all())==1
        plan.stages=dated_stages(plan.stages,today-timedelta(days=8));db.session.commit()
        assert app.extensions['student_management']['sweep']()==1
        assert db.session.get(TuitionControl,eid).status=='overdue' and db.session.get(Enrollment,eid).status=='revoked'
    submit(student,eid,'DATE-BALANCE');review(app,manager,eid)
    with app.app_context():assert db.session.get(TuitionControl,eid).status=='current' and db.session.get(Enrollment,eid).status=='approved'
