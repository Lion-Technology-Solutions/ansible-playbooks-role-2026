from datetime import date,timedelta
from decimal import Decimal
from sqlalchemy import select
from app import db,Cohort,Course,Enrollment,EnrollmentPaymentSchedule,academy_today,available_start_dates,upcoming_cohorts
from start_dates import analytics_starts
from test_portal import register,csrf,request_enrollment
from test_link_payment_plan import setup_plan


def test_all_remaining_2026_and_2027_saturdays():
    dates=analytics_starts(date(2026,11,11))
    assert dates[0]==date(2026,11,14) and dates[-1]==date(2027,12,25)
    assert all(d.weekday()==5 for d in dates)
    assert all(b-a==timedelta(days=7) for a,b in zip(dates,dates[1:]))
    assert analytics_starts(date(2027,12,25))==[date(2027,12,25)]
    assert analytics_starts(date(2028,1,1))==[]


def test_cohort_seed_is_idempotent_and_only_offers_analytics_saturdays(app):
    client=app.test_client();client.get('/apply')
    with app.app_context():
        course=db.session.scalar(select(Course).where(Course.slug=='data-analytics'))
        offered=[c.start_date for c in upcoming_cohorts() if c.course_id==course.id]
        assert offered==available_start_dates(course)
        assert offered[-1]==date(2027,12,25)
        count=len(db.session.scalars(select(Cohort.id)).all());upcoming_cohorts()
        assert count==len(db.session.scalars(select(Cohort.id)).all())
    assert b'data-start-picker' in client.get('/apply?program=data-analytics').data


def test_future_preferred_start_persists_for_existing_student_and_fee_due_now(app):
    app.config['DATA_ANALYTICS_LINK_PLAN']=True
    client=app.test_client();register(client,kind='existing')
    with app.app_context():
        course=db.session.scalar(select(Course).where(Course.slug=='data-analytics'));cid=course.id
        cohort=db.session.scalar(select(Cohort).where(Cohort.course_id==cid,Cohort.start_date==date(2027,12,25)));cohort_id=cohort.id
    client.post('/apply/choose',data={'csrf':csrf(client,'/apply'),'course_id':cid,'cohort_id':cohort_id},follow_redirects=True)
    with app.app_context():
        enrollment=db.session.scalar(select(Enrollment));plan=db.session.get(EnrollmentPaymentSchedule,enrollment.id)
        assert enrollment.cohort_id==cohort_id
        assert plan.total==Decimal('59.97')
        assert [p['due'] for p in plan.stages]==[academy_today().isoformat(),'2028-01-01','2028-02-01','2028-03-01']
        assert plan.stages[0]['first_class']=='2027-12-25'
    # A second selection cannot silently move an existing financial agreement.
    with app.app_context():other=db.session.scalar(select(Cohort.id).where(Cohort.course_id==cid,Cohort.id!=cohort_id))
    client.post('/apply/choose',data={'csrf':csrf(client,'/apply'),'course_id':cid,'cohort_id':other})
    with app.app_context():assert db.session.scalar(select(Enrollment)).cohort_id==cohort_id


def test_server_rejects_sunday_past_out_of_range_and_cross_program(app):
    client=app.test_client();client.get('/apply')
    with app.app_context():
        course=db.session.scalar(select(Course).where(Course.slug=='data-analytics'));cid=course.id
        devops=db.session.scalar(select(Course).where(Course.slug=='devops'))
        ids=[]
        for day in [date(2026,1,3),date(2027,1,3),date(2028,1,1)]:
            row=Cohort(course_id=cid,start_date=day,label='Not offered');db.session.add(row);db.session.flush();ids.append(row.id)
        ids.append(db.session.scalar(select(Cohort.id).where(Cohort.course_id==devops.id)))
        db.session.commit()
    for invalid in ids:
        response=client.post('/apply/choose',data={'csrf':csrf(client,'/apply'),'course_id':cid,'cohort_id':invalid})
        assert response.status_code==400
    register(client)
    for invalid in ids:assert b'Choose a valid upcoming start date' in request_enrollment(client,cid,invalid).data


def test_signup_uses_preferred_2027_start_and_revalidates_on_submit(app):
    client=app.test_client();client.get('/apply')
    with app.app_context():
        cid=db.session.scalar(select(Course.id).where(Course.slug=='data-analytics'))
        cohort_id=db.session.scalar(select(Cohort.id).where(Cohort.course_id==cid,Cohort.start_date==date(2027,2,6)))
    client.post('/apply/choose',data={'csrf':csrf(client,'/apply'),'course_id':cid,'cohort_id':cohort_id})
    page=register(client,kind='existing')
    assert page.status_code==200
    with app.app_context():assert db.session.scalar(select(Enrollment)).cohort_id==cohort_id
