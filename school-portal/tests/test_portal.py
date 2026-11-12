import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit
from sqlalchemy import select
from app import db, Student, StudentIdentity, ProgramProfile, Course, Cohort, Enrollment, ClassSession, ClassRegistration, Attendance, Recording, Assessment, Submission, Notification, now, next_start_dates, seed


def csrf(client, path='/login'):
    response = client.get(path)
    assert response.status_code == 200, response.location
    return re.search(rb'name="csrf" value="([^"]+)"', response.data)[1].decode()


def post(client, path, **fields):
    if path == '/register':
        from legal_content import POLICY_VERSION
        fields.setdefault('policy_acknowledged', 'yes');fields.setdefault('policy_version', POLICY_VERSION)
    page = '/admin' if path.startswith('/admin') else ('/register' if path == '/register' else '/enrollments')
    if path.startswith('/enrollment/') and path.endswith('/payment') and 'student_number' not in fields:
        match=re.search(rb'LT-\d{4}-\d{6,}',client.get('/portal').data)
        if match:fields['student_number']=match[0].decode()
    return client.post(path, data={'csrf': csrf(client, page), **fields}, follow_redirects=True)


def register(client, email='student@example.com', country='Cameroon', kind='new'):
    return post(client, '/register', name='Student Example', email=email, phone='+237600000000',
                country=country, kind=kind, language='en', password='student-password', confirm_password='student-password')


def admin(client):
    return client.post('/admin/login', data={'csrf': csrf(client, '/admin/login'), 'username':'admin', 'password':'test-admin-password'}, follow_redirects=True)


def analytics(app):
    with app.app_context():
        c = db.session.scalar(select(Course).where(Course.slug == 'data-analytics'))
        cohort = db.session.scalar(select(Cohort).where(Cohort.course_id == c.id, Cohort.start_date.in_(next_start_dates())).order_by(Cohort.start_date))
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
    for content in [b'DevOps', b'Cybersecurity', b'Cloud Computing', b'Data Analytics', next_start_dates()[0].strftime('%b %d, %Y').encode(), b'14375756656']:
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
        assert link not in page.data and b'View enrollment payment' in page.data
        assert client.get('/portal').status_code == 200
    existing=app.test_client()
    register(existing,email='existing@example.com',kind='existing')
    page=existing.get('/enrollments')
    assert b'name="cohort_id"' in page.data
    request_enrollment(existing,course,cohort)
    with app.app_context():
        s=db.session.scalar(select(Student).where(Student.email=='existing@example.com'))
        e=db.session.scalar(select(Enrollment).where(Enrollment.student_id==s.id))
        assert e.cohort_id == cohort
        assert s.approved
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
    assert student.get('/portal').status_code == 200
    assert student.get(f'/course/{course}/materials').status_code == 403
    post(manager,'/admin',action='verify_payment',enrollment_id=enrollment_id)
    assert student.get('/portal').status_code == 200
    assert student.get(f'/course/{course}/materials').status_code == 403
    post(manager,'/admin',action='approve',enrollment_id=enrollment_id)
    page=student.get('/portal')
    assert page.status_code == 200
    assert b'yBAAj2k5TM2lEqpMXZY3ZQ' in student.get(f'/course/{course}/register').location.encode()
    assert b'186T95HhFghqKXATdxzXvvu43OWSmBFL2' in student.get(f'/course/{course}/materials').location.encode()
    assert b'Your enrollment is approved' in page.data


def test_student_isolation_and_malicious_form_input(app):
    alice,bob,manager=app.test_client(),app.test_client(),app.test_client()
    register(alice,'alice@example.com')
    register(bob,'bob@example.com')
    admin(manager)
    course,cohort=analytics(app)
    eid=approve(app,alice,manager,course,cohort)
    assert bob.post(f'/enrollment/{eid}/payment',data={'csrf':csrf(bob,'/enrollments'),'reference':'STOLEN'}).status_code == 403
    assert bob.get('/portal').status_code == 200
    assert bob.get(f'/course/{course}/materials').status_code == 403
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
    assert b'Excel live class' in manager.get('/admin?view=classes').data


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
    admin_page=manager.get('/admin?view=classes')
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
    assert student.get('/portal').status_code == 200
    assert student.get(f'/course/{course}/materials').status_code == 403
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


def test_student_numbers_for_new_existing_and_legacy_accounts(app):
    new_client, existing_client = app.test_client(), app.test_client()
    new_page = register(new_client, 'new-number@example.com')
    old_page = register(existing_client, 'existing-number@example.com', kind='existing')
    with app.app_context():
        students = list(db.session.scalars(select(Student).order_by(Student.created_at)))
        numbers = [student.student_number for student in students]
        assert len(set(numbers)) == 2
        assert all(re.fullmatch(r'LT-\d{4}-\d{6,}', number) for number in numbers)
        assert numbers[0].encode() in new_page.data
        assert numbers[1].encode() in old_page.data
        # Simulate an account created before the student-number feature.
        legacy = Student(name='Legacy student',email='legacy@example.com',phone='+237600000001',
                         country='Cameroon',kind='existing',password_hash='not-an-auth-test')
        db.session.add(legacy)
        db.session.commit()
        legacy_id = legacy.id
        with db.engine.begin() as connection:
            seed(connection)
        db.session.expire_all()
        legacy_number = db.session.get(Student, legacy_id).student_number
        assert re.fullmatch(r'LT-\d{4}-\d{6,}', legacy_number)
        with db.engine.begin() as connection:
            seed(connection)
        db.session.expire_all()
        assert db.session.get(Student,legacy_id).student_number == legacy_number
        assert len(list(db.session.scalars(select(StudentIdentity.student_id)))) == 3
        assert [db.session.get(Student,s.id).student_number for s in students] == numbers
    link = re.search(rb'href="(https://wa.me/14375756656\?text=LionTech\+account[^\"]+)"',new_page.data)[1].decode()
    message = parse_qs(urlsplit(link).query)['text'][0]
    assert numbers[0] in message and 'Student Example' in message
    assert b'tap Send' in new_page.data


def test_rolling_weekend_dates_and_toronto_midnight(monkeypatch):
    from datetime import datetime, timezone
    import app as portal_module
    assert next_start_dates(date(2026,11,6)) == [date(2026,11,7),date(2026,11,8)]
    assert next_start_dates(date(2026,11,7)) == [date(2026,11,8),date(2026,11,14)]
    assert next_start_dates(date(2026,11,8)) == [date(2026,11,14),date(2026,11,15)]
    assert next_start_dates(date(2026,12,31)) == [date(2027,1,2),date(2027,1,3)]
    monkeypatch.setattr(portal_module, 'now', lambda: datetime(2026,11,8,1,0,tzinfo=timezone.utc))
    assert portal_module.academy_today() == date(2026,11,7)
    assert next_start_dates() == [date(2026,11,8),date(2026,11,14)]


def test_program_intakes_follow_published_calendar_and_reject_unoffered_date(app):
    student = app.test_client()
    register(student)
    page = student.get('/enrollments')
    with app.app_context():
        courses = list(db.session.scalars(select(Course)))
        for course in courses:
            dates = list(db.session.scalars(select(Cohort.start_date).where(Cohort.course_id==course.id).order_by(Cohort.start_date)))
            from app import available_start_dates
            assert dates == available_start_dates(course)
        devops = next(c for c in courses if c.slug=='devops')
        analytics_course = next(c for c in courses if c.slug=='data-analytics')
        db.session.add(Cohort(course_id=devops.id, start_date=date(2020,1,1), label='Old intake'))
        db.session.commit()
        old_id = db.session.scalar(select(Cohort.id).where(Cohort.start_date==date(2020,1,1)))
        other_id = db.session.scalar(select(Cohort.id).where(Cohort.course_id==analytics_course.id))
        devops_id = devops.id
    assert b'Choose a valid upcoming start date' in request_enrollment(student,devops_id,old_id).data
    assert b'Choose a valid upcoming start date' in request_enrollment(student,devops_id,other_id).data
    for start in next_start_dates():
        assert start.strftime('%A, %B %d, %Y').encode() in page.data
    with app.app_context():
        assert db.session.scalar(select(Enrollment.id)) is None


def test_country_tuition_selected_program_details_and_admin_edit(app):
    student,manager = app.test_client(),app.test_client()
    register(student)
    admin(manager)
    course,cohort = analytics(app)
    page = request_enrollment(student,course,cohort)
    assert b'USD' not in page.data and b'12 weeks' in page.data
    assert b'Curriculum' in page.data and b'Power BI' in page.data
    assert b'My selected programs' in page.data
    assert b'Saturday' in page.data
    tuition_fields = {'tuition_'+str(i):'' for i in range(7)}
    tuition_fields['tuition_0']='12,000 FCFA per month'
    post(manager,'/admin',action='program_profile',course_id=course,duration='4 months',
         schedule='Saturday and Sunday, 4–6 PM America/Toronto',curriculum='Excel\nSQL\nCapstone project',**tuition_fields)
    page = student.get('/enrollments')
    assert b'USD' not in page.data and b'4 months' in page.data
    assert b'local time zone' in page.data and b'Capstone project' in page.data
    assert b'30,000 FCFA' not in page.data
    # A future cold start must preserve the administrator's edited content.
    with app.app_context():
        with db.engine.begin() as connection:
            seed(connection)
        db.session.expire_all()
        assert db.session.get(ProgramProfile,course).duration=='4 months'
    assert student.post('/admin',data={'csrf':csrf(student,'/enrollments'),'action':'program_profile','course_id':course,'duration':'Forged'}).status_code==302
