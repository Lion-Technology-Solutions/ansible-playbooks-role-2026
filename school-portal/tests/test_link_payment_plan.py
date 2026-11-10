import io
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
        assert all(part['due'] is None for part in plan.stages[1:])
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
            if index==0:assert state['next']['amount']==Decimal('9.99') and state['next']['due'] is None
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


def test_deposit_is_not_double_counted_in_forecast_and_no_unspecified_due_revocation(app):
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
