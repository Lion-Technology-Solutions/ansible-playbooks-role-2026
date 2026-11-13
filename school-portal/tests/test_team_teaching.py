import pytest
from sqlalchemy import select
from app import db, Lesson, Enrollment, TaskRecord, StaffTask, TeachingGrant, LessonFile, LessonMedia, Asset
from lesson_teaching import media_url
from test_staff_portal import setup, create, login, post
from test_lessons_files import setup as learner_setup
from test_portal import request_enrollment, csrf, approve


def enrollment(app):
    manager,student,course,cohort=learner_setup(app)
    request_enrollment(student,course,cohort)
    with app.app_context():
        row=db.session.scalar(select(Enrollment));eid=row.id
        lesson=Lesson(course_id=course,title='Safe teaching test',body='Lesson content',published=True)
        db.session.add(lesson);db.session.commit();lid=lesson.id
    return manager,student,course,eid,lid


def test_record_tasks_scoping_reassignment_and_payment_authority(app):
    manager,student,course,eid,lid=enrollment(app)
    manager,sent=setup(app);sid=create(app,'Worker');other_id=create(app,'Unassigned');worker=login(app,sent,sid);other=login(app,sent,other_id)
    result=post(manager,'/staff/workboard',page='/admin/team',member_id=sid,kind='payment',enrollment_ids=[str(eid)],title='Follow up payment',instructions='Contact and escalate',due_on='2027-01-01')
    assert result.status_code==200 and b'Follow up payment' in result.data
    with app.app_context():
        task=db.session.scalar(select(StaffTask));tid=task.id
        assert db.session.get(TaskRecord,tid).enrollment_id==eid
        assert db.session.get(Enrollment,eid).payment_status=='unpaid'
    assert worker.get(f'/staff/tasks/{tid}/record').status_code==200
    assert other.get(f'/staff/tasks/{tid}/record').status_code==403
    assert b'Follow up payment' not in other.get('/staff/workboard').data
    assert post(worker,'/staff/workboard',kind='student',enrollment_ids=[eid],member_id=sid,title='Unauthorized',due_on='2027-01-01').status_code==403
    assert worker.get('/admin?view=payments').status_code==302
    result=post(manager,f'/admin/team/tasks/{tid}/reassign',page='/admin/team',member_id=other_id)
    assert result.status_code==200
    assert worker.get(f'/staff/tasks/{tid}/record').status_code==403
    assert other.get(f'/staff/tasks/{tid}/record').status_code==200
    assert post(other,f'/staff/tasks/{tid}',status='completed',result='Followed up; administrator review requested').status_code==200
    with app.app_context():assert db.session.get(Enrollment,eid).payment_status=='unpaid'


def test_staff_report_history_respects_supervision(app):
    manager,sent=setup(app);sup=create(app,'Supervisor','supervisor');sid=create(app,'Worker',supervisor=sup);oid=create(app,'Other','supervisor')
    worker=login(app,sent,sid);supervisor=login(app,sent,sup);other=login(app,sent,oid)
    assert post(worker,'/staff/reports',report_date='2026-01-01',action='submit',completed='Follow ups',outcomes='Resolved').status_code==200
    path=f'/staff/people/{sid}/reports'
    assert manager.get(path).status_code==200
    assert b'2026-01-01' in supervisor.get(path).data
    assert other.get(path).status_code==403
    assert worker.get(path).status_code==200


def test_instructor_video_upload_link_and_revocation(app):
    manager,student,course,eid,lid=enrollment(app);manager,sent=setup(app)
    sid=create(app,'Teacher','instructor');outsider_id=create(app,'OtherTeacher','instructor');teacher=login(app,sent,sid);other=login(app,sent,outsider_id)
    root=f'/staff/teaching/lessons/{lid}'
    assert teacher.get(root).status_code==403
    assert post(manager,'/staff/teaching',page='/admin/team',member_id=sid,course_id=course,action='grant').status_code==200
    assert teacher.get(root).status_code==200
    assert other.get(root).status_code==403
    assert post(teacher,root,title='YouTube recording',url='https://youtu.be/dQw4w9WgXcQ').status_code==200
    assert post(teacher,root,title='Bad link',url='https://zoom.us.evil.test/j/123').status_code==400
    fields={'csrf':csrf(teacher,root),'filename':'lecture.mp4','size':10,'purpose':'lesson'}
    start=teacher.post(root+'/assets/start',data=fields)
    assert start.status_code==200,start.data
    aid=start.json['id'];headers={'X-CSRF-Token':csrf(teacher,root),'Content-Type':'application/octet-stream'}
    assert other.post(root+f'/assets/{aid}/chunks/0',data=b'0123456789',headers={'X-CSRF-Token':csrf(other,'/staff'), 'Content-Type':'application/octet-stream'}).status_code==403
    assert teacher.post(root+f'/assets/{aid}/chunks/0',data=b'0123456789',headers=headers).status_code==200
    assert post(teacher,root+f'/assets/{aid}/finish').status_code==200
    assert teacher.get('/files/'+aid).data==b'0123456789'
    assert student.get('/files/'+aid).status_code==403
    assert student.get('/lessons/'+str(lid)).status_code==403
    assert b'Present' in teacher.get(root+'/present').data
    assert post(manager,'/staff/teaching',page='/admin/team',member_id=sid,course_id=course,action='revoke').status_code==200
    assert teacher.get(root).status_code==403
    assert teacher.get('/files/'+aid).status_code==302
    with app.app_context():
        e=db.session.get(Enrollment,eid);e.status='approved';e.payment_status='verified';db.session.commit()
    page=student.get('/lessons/'+str(lid));assert page.status_code==200 and b'youtube-nocookie.com/embed/dQw4w9WgXcQ' in page.data
    assert student.get('/files/'+aid+'?download=1').data==b'0123456789'
    assert 'attachment' in student.get('/files/'+aid+'?download=1').headers['Content-Disposition']
    assert student.get('/files/'+aid,headers={'Range':'bytes=2-5'}).status_code==206
    assert student.get(root+'/present').status_code==403
    assert app.test_client().get(root).status_code==403


@pytest.mark.parametrize('url',['javascript:alert(1)','https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ','https://user:pass@zoom.us/j/123','http://youtu.be/dQw4w9WgXcQ','https://youtu.be/invalid','https://zoom.us:444/j/123'])
def test_media_urls_reject_unsafe_providers(url):
    with pytest.raises(ValueError):media_url(url)


def test_assign_student_without_enrollment_and_explicit_teaching_permissions(app):
    from app import Student, StaffMail
    manager,student,course,eid,lid=enrollment(app);manager,sent=setup(app);sid=create(app,'Support');worker=login(app,sent,sid)
    with app.app_context():
        person=Student(name='Not enrolled yet',email='new@example.test',phone='+15555555555',country='Canada',kind='student',password_hash='disabled')
        db.session.add(person);db.session.commit();identity=person.id
    response=post(manager,'/staff/workboard',page='/admin/team',member_id=sid,kind='student',student_ids=[identity],title='Help new student',due_on='2027-01-01')
    assert response.status_code==200
    with app.app_context():
        task=db.session.scalar(select(StaffTask));link=db.session.get(TaskRecord,task.id);tid=task.id
        assert link.student_id==identity and link.enrollment_id is None
        assert db.session.scalar(select(StaffMail).where(StaffMail.kind=='task')).status=='sent'
    assert b'Not enrolled yet' in worker.get(f'/staff/tasks/{tid}/record').data
    assert b'Enrollment:' not in worker.get(f'/staff/tasks/{tid}/record').data
    assert post(worker,'/staff/teaching',member_id=sid,course_id=course,action='grant').status_code==403
    assert post(manager,'/staff/workboard',page='/admin/team',member_id=sid,kind='payment',student_ids=[identity],title='Invalid',due_on='2027-01-01').status_code==400
    assert post(manager,'/staff/workboard',page='/admin/team',member_id=sid,kind='student',student_ids=[identity,'missing'],title='Atomic',due_on='2027-01-01').status_code==404
    with app.app_context():assert len(list(db.session.scalars(select(StaffTask))))==1
