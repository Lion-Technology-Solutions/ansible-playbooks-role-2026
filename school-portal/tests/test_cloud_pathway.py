from datetime import date
from decimal import Decimal
from sqlalchemy import select
from app import db,Course,Cohort,CloudUnit,Enrollment,EnrollmentPaymentSchedule,CoursePaymentPolicy,Lesson
from cloud_curriculum import STAGES,installments,seed_cloud
from pathways import cyber_payment_stages
from test_portal import admin,register,csrf


def test_cloud_levels_lessons_local_prices_and_monthly_amounts(app):
    client,manager=app.test_client(),app.test_client();register(client,country='Canada');admin(manager)
    assert client.get('/courses/cloud-computing').location.endswith('/programs/cloud-computing')
    overview=client.get('/programs/cloud-computing')
    assert overview.status_code==200 and overview.data.count(b'3 monthly installments')==3
    assert b'278.60' in overview.data and b'418.60' in overview.data and b'558.60' in overview.data
    assert b'USD' not in overview.data
    for s in STAGES:
        page=client.get('/programs/cloud-computing/'+s['key'])
        assert page.status_code==200 and page.data.count(b'data-lesson-row')==12
        assert b'108 learning hours' in page.data and b'3 monthly installments' in page.data
        for title in s['titles']:assert title.encode() in page.data
        assert client.get('/programs/cloud-computing/'+s['key']+'?tab=resources').status_code==403
        manager_page=manager.get('/programs/cloud-computing/'+s['key'])
        assert manager_page.status_code==200 and manager_page.data.count(b'Schedule lesson')==12
    assert client.get('/programs/cloud-computing/invalid').status_code==404
    assert client.get('/programs').data.count(b'<article')==4
    assert b'Choose your level' in client.get('/enrollments').data


def test_cloud_installments_dates_full_payment_and_preserved_snapshots(app):
    import app as models
    client=app.test_client();register(client,country='United States')
    for s in STAGES:
        client.get('/programs/cloud-computing/'+s['key']+'/enroll')
        with app.app_context():
            course=db.session.scalar(select(Course).where(Course.slug==s['slug']))
            cid=course.id;cohort=db.session.scalar(select(Cohort).where(Cohort.course_id==cid).order_by(Cohort.start_date));cohortid=cohort.id;start=cohort.start_date
        response=client.post('/programs/cloud-computing/'+s['key']+'/enroll',data={'csrf':csrf(client,'/portal'),'cohort_id':cohortid,'plan':'installments'},follow_redirects=True)
        assert response.status_code==200
        with app.app_context():
            e=db.session.scalar(select(Enrollment).where(Enrollment.course_id==cid));plan=db.session.get(EnrollmentPaymentSchedule,e.id)
            assert plan.total==Decimal(s['price']) and len(plan.stages)==3
            assert [Decimal(p['amount']) for p in plan.stages]==installments(s['price'])
            assert sum(Decimal(p['amount']) for p in plan.stages)==plan.total
            assert all(date.fromisoformat(p['due']).day==7 for p in plan.stages)
            assert plan.stages==cyber_payment_stages(db.session.get(CoursePaymentPolicy,cid).stages,start)
            before=[dict(p) for p in plan.stages]
            with db.engine.begin() as conn:seed_cloud(conn,vars(models));seed_cloud(conn,vars(models))
            db.session.expire_all();assert db.session.get(EnrollmentPaymentSchedule,e.id).stages==before
    assert [p['due'] for p in cyber_payment_stages([{}, {}, {}],date(2027,12,20))]==['2027-12-07','2028-01-07','2028-02-07']
    with app.app_context():
        units=db.session.scalars(select(CloudUnit)).all();assert len(units)==36
        assert all(not u.lesson.published and u.lesson.body for u in units)
    full=app.test_client();register(full,email='cloud-full@example.com',country='Canada')
    with app.app_context():
        c=db.session.scalar(select(Course).where(Course.slug=='cloud-computing-advanced'))
        cid=c.id;cohortid=db.session.scalar(select(Cohort.id).where(Cohort.course_id==cid))
    response=full.post('/programs/cloud-computing/advanced/enroll',data={'csrf':csrf(full,'/portal'),'cohort_id':cohortid,'plan':'full'},follow_redirects=True)
    assert response.status_code==200
    with app.app_context():
        plans=db.session.scalars(select(EnrollmentPaymentSchedule).where(EnrollmentPaymentSchedule.total==399)).all()
        assert any(len(p.stages)==1 and p.stages[0]['amount']=='399.00' for p in plans)
