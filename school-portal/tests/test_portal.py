import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit
from sqlalchemy import select
from app import db, Student, Course, Cohort, Enrollment, ClassSession, ClassRegistration, Attendance, Recording, Assessment, Submission, Notification, now


def csrf(client, path='/login'):
    response = client.get(path)
    assert response.status_code == 200, response.location
    return re.search(rb'name="csrf" value="([^"]+)"', response.data)[1].decode()


def post(client, path, **fields):
    page = '/admin' if path == '/admin' else ('/register' if path == '/register' else '/enrollments')
    return client.post(path, data={'csrf': csrf(client, page), **fields}, follow_redirects=True)


def register(client, email='student@example.com', country='Cameroon', kind='new'):
    return post(client, '/register', name='Student Example', email=email, phone='+237600000000',
                country=country, kind=kind, password='student-password', confirm_password='student-password')


def admin(client):
    return client.post('/admin/login', data={'csrf': csrf(client, '/admin/login'), 'username':'admin', 'password':'test-admin-password'}, follow_redirects=True)


def analytics(app):
    with app.app_context():
        c = db.session.scalar(select(Course).where(Course.slug == 'data-analytics'))
        cohort = db.session.scalar(select(Cohort).where(Cohort.course_id == c.id))
        return c.id, cohort.id


def request_enrollment(client, course, cohort=None):
    return post(client, '/enrollments', course_id=course, **({'cohort_id':cohort} if cohort else {}))


def approve(app, student_client, admin_client, course, cohort=None):
    request_enrollment(student_client, course, cohort)
    with app.app_context():
        e = db.session.scalar(select(Enrollment).where(Enrollment.course_id == course))
        enrollment_id = e.id
    post(student_client, f'/enrollment/{enrollment_id}/payment', reference='TEST-REF-1234', note='Test receipt')
    post(admin_client, '/admin', action='verify_payment', enrollment_id=enrollment_id)
    post(admin_client, '/admin', action='approve', enrollment_id=enrollment_id)
    return enrollment_id


def test_home_brand_courses_and_access_gate(app):
    client = app.test_client()
    page = client.get('/')
    assert page.status_code == 200
    for content in [b'DevOps', b'Cybersecurity', b'Cloud Computing', b'Data Analytics', b'November 14, 2026', b'14375756656']:
        assert content in page.data
    assert client.get('/health').json == {'status':'ok','storage':'test-sqlite'}
    assert client.get('/admin').status_code == 302
    assert client.get('/portal').status_code == 302
    assert b'Zoom recording link' not in page.data
    assert client.post('/register', data={}).status_code == 400
    assert client.get('/static/style.css').status_code == 200


def test_registration_country_payments_dates_and_existing_students(app):
    expected={'Cameroon':b'zx0r0urssqpb','Nigeria':b'7a1b6zlid7fr','Ghana':b'med3t2upqqyt'}
    for country, link in expected.items():
        client=app.test_client()
        page=register(client, email=country.lower()+'@example.com', country=country)
        assert page.status_code == 200
        course, cohort=analytics(app)
        assert b'Choose an available start date' in request_enrollment(client,course).data
        page=request_enrollment(client,course,cohort)
        assert link in page.data
        assert client.get('/portal').location.endswith('/enrollments')
    existing=app.test_client()
    register(existing,email='existing@example.com',kind='existing')
    page=existing.get('/enrollments')
    assert b'name="cohort_id"' not in page.data
    request_enrollment(existing,course)
    with app.app_context():
        s=db.session.scalar(select(Student).where(Student.email=='existing@example.com'))
        e=db.session.scalar(select(Enrollment).where(Enrollment.student_id==s.id))
        assert e.cohort_id is None
        assert not s.approved
        assert 'student-password' not in s.password_hash
    duplicate=app.test_client()
    assert b'already has an account' in register(duplicate,email='existing@example.com').data


def test_payment_must_be_verified_before_approval(app):
    student, manager=app.test_client(),app.test_client()
    register(student)
    admin(manager)
    course,cohort=analytics(app)
    request_enrollment(student,course,cohort)
    with app.app_context():
        enrollment_id=db.session.scalar(select(Enrollment.id))
    result=post(manager,'/admin',action='approve',enrollment_id=enrollment_id)
    assert b'Verify the enrollment payment' in result.data
    assert b'must submit a payment reference' in post(manager,'/admin',action='verify_payment',enrollment_id=enrollment_id).data
    post(student,f'/enrollment/{enrollment_id}/payment',reference='MY-REAL-TRANSACTION')
    assert student.get('/portal').status_code == 302
    post(manager,'/admin',action='verify_payment',enrollment_id=enrollment_id)
    assert student.get('/portal').status_code == 302
    post(manager,'/admin',action='approve',enrollment_id=enrollment_id)
    page=student.get('/portal')
    assert page.status_code == 200
    assert b'yBAAj2k5TM2lEqpMXZY3ZQ' in page.data
    assert b'186T95HhFghqKXATdxzXvvu43OWSmBFL2' in page.data
    assert b'Your enrollment is approved' in page.data


def test_student_isolation_and_malicious_form_input(app):
    alice,bob,manager=app.test_client(),app.test_client(),app.test_client()
    register(alice,'alice@example.com')
    register(bob,'bob@example.com')
    admin(manager)
    course,cohort=analytics(app)
    eid=approve(app,alice,manager,course,cohort)
    assert bob.post(f'/enrollment/{eid}/payment',data={'csrf':csrf(bob,'/enrollments'),'reference':'STOLEN'}).status_code == 403
    assert bob.get('/portal').status_code == 302
    assert alice.post('/admin',data={'csrf':csrf(alice,'/portal'),'action':'approve','enrollment_id':eid}).status_code == 302
    with app.app_context():
        other=db.session.scalar(select(Course).where(Course.slug=='devops'))
        task=Assessment(course_id=other.id,title='Private DevOps Quiz',kind='quiz',instructions='Q1')
        db.session.add(task)
        db.session.commit()
        task_id=task.id
    assert b'Private DevOps Quiz' not in alice.get('/portal').data
    assert alice.post(f'/assessment/{task_id}/submit',data={'csrf':csrf(alice,'/portal'),'answer':'Wrong course'}).status_code == 403
    assert alice.post('/logout',data={'csrf':'\u2603'}).status_code == 400


def test_classes_attendance_and_whatsapp(app):
    student,manager=app.test_client(),app.test_client()
    register(student)
    admin(manager)
    course,cohort=analytics(app)
    approve(app,student,manager,course,cohort)
    start=(now()-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
    post(manager,'/admin',action='class',course_id=course,title='Excel live class',starts_at=start,registration_url='https://zoom.us/test')
    with app.app_context():
        lesson_id=db.session.scalar(select(ClassSession.id))
    page=student.post(f'/class/{lesson_id}/register',data={'csrf':csrf(student,'/portal')},follow_redirects=True)
    assert b'Complete Zoom registration' in page.data
    page=student.post('/attendance',data={'csrf':csrf(student,'/attendance'),'class_id':lesson_id},follow_redirects=True)
    assert b'ATTENDANCE RECORDED' in page.data
    link=re.search(rb'href="(https://wa.me/14375756656\?text=[^"]+)"',page.data)[1].decode()
    message=parse_qs(urlsplit(link).query)['text'][0]
    assert 'Student Example' in message and 'Data Analytics' in message and 'Excel live class' in message
    assert 'Tap' in page.get_data(as_text=True)
    student.post('/attendance',data={'csrf':csrf(student,'/attendance'),'class_id':lesson_id})
    with app.app_context():
        assert len(db.session.scalars(select(Attendance)).all())==1
        assert len(db.session.scalars(select(ClassRegistration)).all())==1
    assert b'Excel live class' in manager.get('/admin').data


def test_recordings_notifications_schedule_and_grading(app):
    student,manager=app.test_client(),app.test_client()
    register(student)
    admin(manager)
    course,cohort=analytics(app)
    approve(app,student,manager,course,cohort)
    post(manager,'/admin',action='recording',course_id=course,title='Latest analytics recording',url='https://zoom.us/recording',available_at='')
    page=student.get('/portal')
    assert b'Latest analytics recording' in page.data and b'New class recording' in page.data
    future=(now()+timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')
    post(manager,'/admin',action='recording',course_id=course,title='Future recording',url='https://zoom.us/future',available_at=future)
    assert b'Future recording' not in student.get('/portal').data
    post(manager,'/admin',action='assessment',course_id=course,title='SQL Quiz',kind='quiz',instructions='Write a SELECT query.',due_at='')
    with app.app_context():
        assessment_id=db.session.scalar(select(Assessment.id))
    page=student.post(f'/assessment/{assessment_id}/submit',data={'csrf':csrf(student,'/portal'),'answer':'<script>alert(1)</script> SELECT * FROM learners','url':'https://github.com/example/project'},follow_redirects=True)
    assert page.status_code == 200
    admin_page=manager.get('/admin')
    assert b'&lt;script&gt;' in admin_page.data and b'<script>alert' not in admin_page.data
    with app.app_context():
        submission_id=db.session.scalar(select(Submission.id))
    post(manager,'/admin',action='grade',submission_id=submission_id,score='94',feedback='Excellent work')
    page=student.get('/portal')
    assert b'94/100' in page.data and b'Excellent work' in page.data and b'Your result is ready' in page.data
    student.post(f'/assessment/{assessment_id}/submit',data={'csrf':csrf(student,'/portal'),'answer':'Revised answer','url':''})
    with app.app_context():
        assert db.session.get(Submission,submission_id).score is None
    assert b'Enter a valid https or http link' in post(manager,'/admin',action='recording',course_id=course,title='Bad',url='javascript:alert(1)',available_at='').data


def test_revocation_password_reset_and_durable_records(app):
    student,manager=app.test_client(),app.test_client()
    register(student)
    admin(manager)
    course,cohort=analytics(app)
    eid=approve(app,student,manager,course,cohort)
    with app.app_context():
        sid=db.session.scalar(select(Student.id))
    post(manager,'/admin',action='reset_password',student_id=sid,password='new-student-password')
    assert student.get('/portal').status_code == 302
    student.post('/login',data={'csrf':csrf(student),'email':'student@example.com','password':'new-student-password'})
    assert student.get('/portal').status_code == 200
    post(manager,'/admin',action='reject',enrollment_id=eid)
    assert b'yBAAj2k5TM2lEqpMXZY3ZQ' not in student.get('/portal').data
    with app.app_context():
        assert db.session.get(Student,sid).email=='student@example.com'
        assert db.session.get(Enrollment,eid).status=='rejected'


def test_invalid_and_duplicate_references_and_cross_course_dates(app):
    one,two=app.test_client(),app.test_client()
    register(one,'one@example.com')
    register(two,'two@example.com')
    course,cohort=analytics(app)
    request_enrollment(one,course,cohort)
    request_enrollment(two,course,cohort)
    with app.app_context():
        ids=list(db.session.scalars(select(Enrollment.id).order_by(Enrollment.id)))
    post(one,f'/enrollment/{ids[0]}/payment',reference='REUSED-REF')
    page=post(two,f'/enrollment/{ids[1]}/payment',reference='REUSED-REF')
    assert b'already been submitted' in page.data
