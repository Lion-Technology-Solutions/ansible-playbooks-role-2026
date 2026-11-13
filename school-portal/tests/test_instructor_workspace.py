from datetime import timedelta
from sqlalchemy import select
from app import db,Course,Lesson,TeachingGrant,ClassSession,Assessment,Enrollment,Student,Attendance,Submission,now
from test_staff_portal import setup,create,login,post
from test_portal import register,csrf


def test_only_instructors_manage_media_and_course_scoped_classes_reviews(app):
    manager,sent=setup(app);student=app.test_client();register(student)
    sid=create(app,'AssignedTeacher','instructor');other_id=create(app,'OtherTeacher','instructor');teacher=login(app,sent,sid);other=login(app,sent,other_id)
    with app.app_context():
        course=db.session.scalar(select(Course).where(Course.slug=='data-analytics'));cid=course.id
        other_course=db.session.scalar(select(Course).where(Course.slug=='devops'));other_cid=other_course.id
        lesson=Lesson(course_id=cid,title='Instructor lesson',published=True);db.session.add(lesson);db.session.add(TeachingGrant(member_id=sid,course_id=cid));db.session.flush();lid=lesson.id;db.session.commit()
    assert manager.get(f'/staff/teaching/lessons/{lid}').status_code==403
    assert manager.post('/admin/assets/start',data={'csrf':csrf(manager,'/admin'),'filename':'clip.mp4','size':10,'purpose':'lesson'}).status_code==403
    assert post(manager,'/admin',page='/admin',action='recording',course_id=cid,title='YouTube bypass attempt',url='https://youtu.be/dQw4w9WgXcQ',available_at='').status_code==403
    assert teacher.get(f'/staff/teaching/lessons/{lid}').status_code==200
    assert teacher.get('/teaching/workspace?course_id='+str(other_cid)).status_code==403
    assert other.get('/teaching/workspace?course_id='+str(cid)).status_code==403
    fields=dict(action='class',course_id=cid,title='Instructor scheduled class',lesson_id=lid,starts_at=(now()-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),timezone='UTC',join_url='https://zoom.us/j/123456789',duration='120')
    assert post(teacher,'/teaching/workspace',**fields).status_code==200
    with app.app_context():classroom=db.session.scalar(select(ClassSession).where(ClassSession.title=='Instructor scheduled class'));class_id=classroom.id;assert classroom.course_id==cid
    assert post(other,'/teaching/workspace',**fields).status_code==403
    with app.app_context():
        person=db.session.scalar(select(Student));student_id=person.id
        e=Enrollment(student_id=student_id,course_id=cid,status='approved',payment_status='verified');db.session.add(e);db.session.commit()
    assert post(teacher,f'/teaching/classes/{class_id}/attendance',student_ids=[student_id]).status_code==200
    assert other.get(f'/teaching/classes/{class_id}/attendance').status_code==403
    with app.app_context():assert db.session.scalar(select(Attendance).where(Attendance.class_id==class_id)).student_id==student_id
    assert post(teacher,'/teaching/workspace',action='assessment',course_id=cid,title='Instructor assignment',kind='assignment',instructions='Explain the process',lesson_id=lid).status_code==200
    with app.app_context():
        assessment=db.session.scalar(select(Assessment).where(Assessment.title=='Instructor assignment'));aid=assessment.id
        submission=Submission(student_id=student_id,assessment_id=aid,answer='Student answer');db.session.add(submission);db.session.commit();subid=submission.id
    assert other.get(f'/teaching/assessments/{aid}/reviews').status_code==403
    assert post(teacher,f'/teaching/assessments/{aid}/reviews',submission_id=subid,score='85',feedback='Clear explanation').status_code==200
    with app.app_context():assert db.session.get(Submission,subid).score==85
    assert student.get('/teaching/workspace').status_code==403


def test_linked_student_still_requires_own_mfa(app):
    import pyotp
    from test_member_profiles import setup_person,verify_switch
    from app import AuthFactor
    manager,sent,sid,student_id=setup_person(app)
    secret=pyotp.random_base32()
    with app.app_context():
        factor=AuthFactor(id='f'*32,student_id=student_id,method='totp',ciphertext='')
        factor.ciphertext=app.extensions['integrations']['seal']('auth_factor',factor.id,{'secret':secret});db.session.add(factor);db.session.commit()
    teacher=login(app,sent,sid)
    response=verify_switch(app,teacher,'student:'+student_id)
    assert response.status_code==200
    with teacher.session_transaction() as data:assert data.get('pending_auth') and not data.get('student_id') and not data.get('admin')
    assert teacher.get('/portal').status_code==302

    result=post(teacher,'/account/security/challenge',page='/account/security/challenge',action='verify',factor_id='f'*32,code=pyotp.TOTP(secret).now())
    assert result.status_code==200
    with teacher.session_transaction() as data:assert data.get('student_id')==student_id and data.get('linked_profiles') and not data.get('admin') and not data.get('staff_id')
    assert teacher.get('/portal').status_code==200
