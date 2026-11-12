from datetime import date,timedelta
from decimal import Decimal
from sqlalchemy import select
from app import db,Course,Cohort,Enrollment,EnrollmentPaymentSchedule,CoursePaymentPolicy,FeeInvoice
from devops_pathway import STAGES,PAYMENTS,payment_dates,seed_devops
from test_portal import admin,register,csrf


def test_devops_levels_share_enrollment_and_show_localized_terms(app):
    client,manager=app.test_client(),app.test_client();register(client,country='Canada');admin(manager)
    assert client.get('/courses/devops').location.endswith('/programs/devops')
    page=client.get('/programs/devops')
    assert page.status_code==200 and page.data.count(b'558.60 / month')==3
    assert b'3,351.60' in page.data and b'138.60' in page.data and b'USD' not in page.data
    for stage in STAGES:
        path='/programs/devops/'+stage['key']
        page=client.get(path)
        assert page.status_code==200 and page.data.count(b'data-lesson-row')==16
        assert b'One plan. All three levels.' in page.data and b'420.00' in page.data
        assert b'Saturday and Sunday' in page.data
        assert client.get(path+'?tab=resources').status_code==403
        assert manager.get(path).data.count(b'Manage &amp; schedule lesson')==16
        assert client.get(path+'/enroll').status_code==200
    with app.app_context():
        cid=db.session.scalar(select(Course.id).where(Course.slug=='devops'))
        cohort=db.session.scalar(select(Cohort).where(Cohort.course_id==cid).order_by(Cohort.start_date));cohortid=cohort.id;start=cohort.start_date
    response=client.post('/programs/devops/foundation/enroll',data={'csrf':csrf(client,'/portal'),'cohort_id':cohortid,'plan':'installments'},follow_redirects=True)
    assert response.status_code==200
    with app.app_context():
        e=db.session.scalar(select(Enrollment).where(Enrollment.course_id==cid));eid=e.id
        snapshot=db.session.get(EnrollmentPaymentSchedule,eid)
        assert snapshot.total==Decimal('2394.00')
        assert [Decimal(p['amount']) for p in snapshot.stages]==[Decimal(99),Decimal(300)]+[Decimal(399)]*5
        assert snapshot.stages[1]['due']==(start + timedelta(days=7)).isoformat()
        assert db.session.scalar(select(FeeInvoice).where(FeeInvoice.enrollment_id==eid)).amount_usd==99
        old=[dict(p) for p in snapshot.stages]
        e.status='approved';e.payment_status='verified';db.session.commit()
        policy=db.session.get(CoursePaymentPolicy,cid);policy.total=Decimal(3000);db.session.commit()
        import app as models
        with db.engine.begin() as conn:seed_devops(conn,vars(models))
    for stage in ('foundation','intermediate','advanced'):
        assert client.get('/programs/devops/'+stage+'?tab=resources').status_code==200
    for stage in ('intermediate','advanced'):
        response=client.post('/programs/devops/'+stage+'/enroll',data={'csrf':csrf(client,'/portal'),'cohort_id':cohortid,'plan':'installments'},follow_redirects=True)
        assert response.status_code==200
    with app.app_context():
        assert len(db.session.scalars(select(Enrollment).where(Enrollment.course_id==cid)).all())==1
        snapshot=db.session.get(EnrollmentPaymentSchedule,eid)
        assert snapshot.stages==old and snapshot.total==2394


def test_devops_deadlines_are_chronological_and_roll_over_years():
    for start,expected in [(date(2027,1,30),['2027-02-06','2027-03-01','2027-04-01']), (date(2027,12,28),['2028-01-04','2028-02-01','2028-03-01']), (date(2027,2,22),['2027-03-01','2027-04-01','2027-05-01'])]:
        parts=payment_dates(PAYMENTS,start,date(2027,1,1))
        assert [p['due'] for p in parts[1:4]]==expected
        assert len(parts)==7 and sum(Decimal(p['amount']) for p in parts)==2394
        assert all(p['due'].endswith('-01') for p in parts[2:])
    assert payment_dates(PAYMENTS,None,date(2027,1,1))[1]['due'] is None
