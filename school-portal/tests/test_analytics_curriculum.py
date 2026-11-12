from decimal import Decimal
from datetime import timedelta
from sqlalchemy import select
from app import (db, Course, Cohort, Enrollment, Lesson, AnalyticsUnit, ClassSession,
                 ClassJoinLink, ScheduledClass, LessonClass, EnrollmentPaymentSchedule,
                 CoursePaymentPolicy, PaymentEntry, GlobalTuition, now)
from analytics_curriculum import STAGES
from test_portal import admin, register, csrf, post, request_enrollment
from test_link_payment_plan import submit, review
from test_lessons_files import MemoryStorage, upload


def setup(app):
    app.config.update(DATA_ANALYTICS_LINK_PLAN=True, FILE_STORAGE=MemoryStorage())
    manager, student=app.test_client(),app.test_client()
    admin(manager);register(student,country='Canada')
    with app.app_context():
        courses={c.slug:c.id for c in db.session.scalars(select(Course))}
        cohorts={s['slug']:db.session.scalar(select(Cohort.id).where(Cohort.course_id==courses[s['slug']])) for s in STAGES}
    return manager,student,courses,cohorts


def enroll(student,app,cid,cohort):
    request_enrollment(student,cid,cohort)
    with app.app_context():return db.session.scalar(select(Enrollment.id).where(Enrollment.course_id==cid))


def open_lesson(manager,stage,number):
    response=post(manager,f'/admin/data-analytics/{stage}/lessons/{number}/open')
    assert response.status_code==200,response.data[:1000]
    assert b'Data Analytics curriculum' in response.data
    return int(response.request.path.split('/')[3])


def test_all_three_catalogues_visible_and_student_prices_are_local(app):
    manager,student,courses,cohorts=setup(app)
    response=student.get('/data-analytics/lessons?stage=all')
    assert response.status_code==200
    assert response.data.count(b'data-lesson-row')==20
    assert b'USD' not in response.data
    assert b'418.60' in response.data and b'558.60' in response.data
    for stage in STAGES:
        body=student.get('/data-analytics/lessons?stage='+stage['key']).data
        assert body.count(b'data-lesson-row')==stage['weeks']
        for title in stage['titles']:
            assert title.replace('&','&amp;').encode() in body
    body=manager.get('/data-analytics/lessons').data
    assert body.count(b'data-lesson-row')==20 and b'$299.00' in body and b'$399.00' in body
    assert app.test_client().get('/data-analytics/lessons').status_code==302
    assert student.get('/data-analytics/lessons?stage=invalid').status_code==404
    assert student.post('/admin/data-analytics/foundation/lessons/1/open',data={'csrf':csrf(student,'/portal')}).status_code==302


def test_stage_payments_unlock_only_their_own_lessons_and_schedules(app):
    from app import LessonFile
    manager,student,courses,cohorts=setup(app)
    ids={};lessons={};classes={};assets={}
    for stage in STAGES:
        key=stage['key'];cid=courses[stage['slug']]
        ids[key]=enroll(student,app,cid,cohorts[stage['slug']])
        lessons[key]=open_lesson(manager,key,stage['offset']+1)
        assets[key]=upload(manager,key+'.xlsx',b'PK sample')
        with app.app_context():
            lesson=db.session.get(Lesson,lessons[key]);lesson.published=True;lesson.body='PRIVATE-'+key
            db.session.add(LessonFile(lesson_id=lesson.id,asset_id=assets[key]))
            c=ClassSession(course_id=cid,title='SCHEDULE-'+key,starts_at=now()+timedelta(days=3));c.joining=ClassJoinLink(url='https://zoom.us/j/'+key)
            db.session.add(c);db.session.flush();classes[key]=c.id
            db.session.add(ScheduledClass(class_id=c.id,cohort_id=cohorts[stage['slug']],published=True,timezone='UTC',duration=120,join_minutes=15,revision=1))
            db.session.add(LessonClass(class_id=c.id,lesson_id=lesson.id));db.session.commit()
        student.get('/payments/enrollment/'+str(ids[key]),follow_redirects=True)
    assert b'SCHEDULE-' not in student.get('/portal').data
    assert b'SCHEDULE-' not in student.get('/live-classes').data
    for stage in STAGES:
        key=stage['key']
        assert student.get('/lessons/'+str(lessons[key])).status_code==403
        assert student.get('/files/'+assets[key]).status_code==403
        assert student.get('/calendar/classes/'+str(classes[key])+'.ics').status_code==403
    submit(student,ids['foundation'],'FOUNDATION-10');review(app,manager,ids['foundation'])
    assert b'SCHEDULE-foundation' in student.get('/live-classes').data
    assert b'SCHEDULE-intermediate' not in student.get('/live-classes').data
    assert student.get('/files/'+assets['foundation']).status_code==200
    for key,amount in [('intermediate','299.00'),('advanced','399.00')]:
        with app.app_context():
            plan=db.session.get(EnrollmentPaymentSchedule,ids[key]);assert len(plan.stages)==1
            assert plan.total==Decimal(amount) and plan.stages[0]['amount']==amount
            assert plan.stages[0]['due'] and plan.stages[0]['first_class']
        submit(student,ids[key],key+'-FULL')
        assert student.get('/lessons/'+str(lessons[key])).status_code==403
        review(app,manager,ids[key])
        assert student.get('/lessons/'+str(lessons[key])).status_code==200
        assert student.get('/files/'+assets[key]).status_code==200
        assert student.get('/calendar/classes/'+str(classes[key])+'.ics').status_code==200
    with app.app_context():
        e=db.session.get(Enrollment,ids['advanced']);e.status='revoked';db.session.commit()
    assert b'SCHEDULE-advanced' not in student.get('/live-classes').data
    assert student.get('/files/'+assets['advanced']).status_code==403
    assert student.get('/data-analytics/lessons?stage=all').data.count(b'data-lesson-row')==20


def test_seeding_preserves_existing_plans_and_is_idempotent(app):
    from analytics_portal import seed_stages
    from program_link_plans import seed_policy
    import app as models
    manager,student,courses,cohorts=setup(app)
    eid=enroll(student,app,courses['data-analytics'],cohorts['data-analytics'])
    student.get('/payments/enrollment/'+str(eid),follow_redirects=True)
    with app.app_context():
        plan=db.session.get(EnrollmentPaymentSchedule,eid)
        plan.total=Decimal('59.97');parts=[dict(p) for p in plan.stages];parts[-1]['amount']='19.99';plan.stages=parts
        db.session.add(PaymentEntry(enrollment_id=eid,amount=Decimal('10.00'),currency='USD',reference='LEGACY-PAYMENT'));db.session.commit()
        with db.engine.begin() as connection:
            for _ in range(2):seed_policy(connection,vars(models));seed_stages(connection,vars(models))
        db.session.expire_all()
        assert db.session.get(EnrollmentPaymentSchedule,eid).total==Decimal('59.97')
        assert db.session.get(EnrollmentPaymentSchedule,eid).stages[-1]['amount']=='19.99'
        assert db.session.scalar(select(PaymentEntry.amount))==Decimal('10.00')
        assert db.session.get(CoursePaymentPolicy,courses['data-analytics']).total==Decimal('59.99')
        assert len(db.session.scalars(select(Course).where(Course.slug.like('data-analytics%'))).all())==3
        assert len(db.session.scalars(select(CoursePaymentPolicy)).all())==3


def test_every_week_opens_its_own_draft_workspace(app):
    manager,student,courses,cohorts=setup(app)
    seen=set()
    for stage in STAGES:
        for number in range(stage['offset']+1,stage['offset']+stage['weeks']+1):
            lid=open_lesson(manager,stage['key'],number)
            assert lid not in seen;seen.add(lid)
    assert open_lesson(manager,'foundation',1) in seen
    with app.app_context():
        units=db.session.scalars(select(AnalyticsUnit)).all()
        assert len(units)==20
        assert all(not u.lesson.published and u.lesson.body for u in units)
    for invalid in [('foundation',13),('intermediate',1),('advanced',21),('unknown',1)]:
        assert manager.post(f'/admin/data-analytics/{invalid[0]}/lessons/{invalid[1]}/open',data={'csrf':csrf(manager,'/admin')}).status_code==404
