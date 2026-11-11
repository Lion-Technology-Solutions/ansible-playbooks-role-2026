from datetime import timedelta
from sqlalchemy import select
from app import db, Course, Cohort, Enrollment, Lesson, DevOpsUnit, ClassSession, ClassJoinLink, ScheduledClass, LessonClass, LessonActivity, Recording, now
from test_portal import admin, register, csrf, post, request_enrollment
from test_lessons_files import upload


def setup(app):
    manager, student = app.test_client(), app.test_client()
    admin(manager); register(student)
    with app.app_context():
        course = db.session.scalar(select(Course).where(Course.slug == 'devops'))
        cohort = db.session.scalar(select(Cohort).where(Cohort.course_id == course.id))
        return manager, student, course.id, cohort.id


def open_lesson(manager, number=27):
    response = post(manager, f'/admin/devops/lessons/{number}/open')
    assert response.status_code == 200
    assert b'Lesson overview' in response.data
    return int(response.request.path.split('/')[3])


def test_catalogue_visible_after_signup_but_private_content_never_leaks(app):
    manager, student, cid, cohort = setup(app)
    lid = open_lesson(manager)
    request_enrollment(student, cid, cohort)
    with app.app_context():
        lesson = db.session.get(Lesson, lid)
        lesson.body = 'PRIVATE-LAB-INSTRUCTIONS';lesson.published = True
        meeting = ClassSession(course_id=cid, title='PRIVATE-SCHEDULE-TITLE', starts_at=now()+timedelta(days=2))
        meeting.joining = ClassJoinLink(url='https://zoom.us/j/SECRET-JOIN')
        db.session.add(meeting); db.session.flush()
        db.session.add(ScheduledClass(class_id=meeting.id, cohort_id=cohort, published=True, timezone='UTC',duration=120,join_minutes=15,revision=1))
        db.session.add(LessonClass(class_id=meeting.id, lesson_id=lid))
        db.session.commit();class_id = meeting.id
    catalogue=student.get('/devops/lessons')
    assert catalogue.status_code==200
    assert catalogue.data.count(b'data-lesson-row')==48
    assert b'DevOps culture &amp; delivery lifecycle' in catalogue.data
    assert b'Capstone demo, portfolio &amp; career readiness' in catalogue.data
    for path in ['/devops/lessons','/portal','/live-classes']:
        body=student.get(path).data
        for private in [b'PRIVATE-LAB',b'PRIVATE-SCHEDULE',b'SECRET-JOIN']:
            assert private not in body
    with app.test_request_context('/assistant'):
        from flask import g
        from app import Student
        g.student=db.session.scalar(select(Student))
        assert app.extensions['assistant']['context'](False,cid)['classes']==[]
    assert student.get(f'/lessons/{lid}').status_code==403
    for path in [f'/classes/{class_id}',f'/class/{class_id}/join',f'/calendar/classes/{class_id}.ics']:
        assert student.get(path).status_code==403
    assert app.test_client().get('/devops/lessons').status_code==302
    assert student.get(f'/admin/lessons/{lid}/workspace').status_code==302


def test_verified_payment_and_approval_are_both_required_and_revocable(app):
    manager, student, cid, cohort = setup(app)
    lid = open_lesson(manager, 1)
    request_enrollment(student,cid,cohort)
    with app.app_context():
        e=db.session.scalar(select(Enrollment).where(Enrollment.course_id==cid));eid=e.id
        lesson=db.session.get(Lesson,lid);lesson.published=True;lesson.body='RELEASED-NOTES'
        c=ClassSession(course_id=cid,title='PAID-ONLY-CLASS',starts_at=now()+timedelta(days=1))
        db.session.add(c);db.session.flush();class_id=c.id
        db.session.add(LessonClass(class_id=c.id,lesson_id=lid));db.session.commit()
    for status,payment,allowed in [('pending','verified',False),('approved','unpaid',False),('approved','verified',True),('rejected','verified',False)]:
        with app.app_context():
            e=db.session.get(Enrollment,eid);e.status=status;e.payment_status=payment;db.session.commit()
        assert student.get('/devops/lessons').data.count(b'data-lesson-row')==48
        assert (b'PAID-ONLY-CLASS' in student.get('/live-classes').data)==allowed
        assert student.get(f'/lessons/{lid}').status_code==(200 if allowed else 403)
        assert student.get(f'/calendar/classes/{class_id}.ics').status_code==(200 if allowed else 403)


def test_admin_workspace_resources_assessments_and_recordings_are_lesson_specific(app):
    manager, student, cid, cohort = setup(app)
    lid=open_lesson(manager)
    assert open_lesson(manager)==lid
    other=open_lesson(manager,28)
    with app.app_context():
        assert len(db.session.scalars(select(DevOpsUnit)).all())==2
    for kind in ['assignment','quiz']:
        response=post(manager,'/admin',action='assessment',course_id=cid,return_lesson_id=lid,kind=kind,title=f'Lesson 27 {kind}',instructions='Explain rolling updates.',due_at='')
        assert response.status_code==200
        assert f'Lesson 27 {kind}'.encode() in response.data
    response=post(manager,'/admin',action='recording',course_id=cid,return_lesson_id=lid,title='Lesson 27 replay',url='https://example.com/replay',available_at='')
    assert b'Lesson 27 replay' in response.data
    assert b'Lesson 27 replay' not in manager.get(f'/admin/lessons/{other}/workspace').data
    assert manager.get(f'/admin/live-classes?lesson_id={lid}').status_code==200
    with app.app_context():
        assert len(db.session.scalars(select(LessonActivity)).all())==3


def test_all_resource_formats_remain_private_attachment_downloads(app):
    from test_lessons_files import MemoryStorage
    manager, student, cid, cohort=setup(app)
    app.config['FILE_STORAGE']=MemoryStorage()
    for filename in ['architecture.drawio','sources.zip','diagram.svg','example.html','Dockerfile']:
        aid=upload(manager,filename,b'<script>example</script>')
        response=manager.get(f'/files/{aid}')
        assert response.status_code==200
        assert 'attachment' in response.headers['Content-Disposition']
        assert response.mimetype=='application/octet-stream'
        assert student.get(f'/files/{aid}').status_code==403


def test_linked_schedule_draft_and_notification_revocation(app):
    from app import ClassNoticeScope, Notification
    from test_global_portal import schedule
    manager,student,cid,cohort=setup(app)
    lid=open_lesson(manager,27)
    request_enrollment(student,cid,cohort)
    with app.app_context():
        e=db.session.scalar(select(Enrollment).where(Enrollment.course_id==cid));eid=e.id
        e.status='approved';e.payment_status='verified';db.session.commit()
    result=schedule(manager,cid,cohort,title='SCOPED-PRIVATE-DATE',lesson_id=lid,state='draft')
    assert result.status_code==200
    with app.app_context():
        c=db.session.scalar(select(ClassSession));class_id=c.id
        assert db.session.get(LessonClass,class_id).lesson_id==lid
    assert b'SCOPED-PRIVATE-DATE' not in student.get('/live-classes').data
    schedule(manager,cid,cohort,title='SCOPED-PRIVATE-DATE',lesson_id=lid,class_id=class_id,state='published')
    assert b'SCOPED-PRIVATE-DATE' in student.get('/live-classes').data
    with app.app_context():
        assert db.session.scalar(select(ClassNoticeScope))
        notices=db.session.scalars(select(Notification).where(Notification.title=='Class schedule updated')).all()
        assert notices
        for n in notices:assert app.extensions['devops']['notice_visible'](n,db.session.get(Enrollment,eid).student)
        e=db.session.get(Enrollment,eid);e.payment_status='unpaid';db.session.commit()
    assert b'SCOPED-PRIVATE-DATE' not in student.get('/portal').data
    assert b'SCOPED-PRIVATE-DATE' not in student.get('/live-classes').data
    with app.test_request_context('/assistant'):
        from flask import g
        from app import Student
        g.student=db.session.scalar(select(Student))
        assert app.extensions['assistant']['context'](False,cid)['classes']==[]
