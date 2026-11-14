from test_portal import historical_payment
from datetime import timedelta
from urllib.parse import parse_qs,urlsplit
from sqlalchemy import select

from app import (db,Student,Enrollment,Course,ClassSession,Recording,PublicUpdate,
                 AccountRequest,ClassJoinLink,Cohort,Notification,now,next_start_dates,seed)
from test_portal import admin,analytics,csrf,post,register,request_enrollment


def setup_pending(app):
    student,manager=app.test_client(),app.test_client()
    register(student)
    admin(manager)
    course,cohort=analytics(app)
    request_enrollment(student,course,cohort)
    post(manager,'/admin',action='class',course_id=course,title='Paid Analytics Class',
         starts_at=(now()+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
         registration_url='https://zoom.us/register/private-class',join_url='https://zoom.us/j/private-meeting')
    post(manager,'/admin',action='recording',course_id=course,title='Paid course recording',
         url='https://zoom.us/rec/private-recording',available_at='')
    with app.app_context():
        eid=db.session.scalar(select(Enrollment.id))
        lesson_id=db.session.scalar(select(ClassSession.id))
        rid=db.session.scalar(select(Recording.id))
    return student,manager,course,eid,lesson_id,rid


def assert_locked(client,course,lesson,rid):
    page=client.get('/portal')
    assert page.status_code==200
    for secret in [b'/register/private-class',b'/j/private-meeting',b'/rec/private-recording',b'186T95HhFghqKXATdxzXvvu43OWSmBFL2']:
        assert secret not in page.data
    assert b'Access class materials' not in page.data
    assert b'Join class' not in page.data
    assert b'Register for live class' not in page.data
    assert client.get(f'/course/{course}/register').status_code==403
    assert client.get(f'/course/{course}/materials').status_code==403
    assert client.get(f'/class/{lesson}/join').status_code==403
    assert client.get(f'/recording/{rid}/watch').status_code==403
    assert client.post(f'/class/{lesson}/register',data={'csrf':csrf(client,'/portal')}).status_code==403


def test_active_accounts_show_courses_payments_and_announcements_but_hide_schedule_before_payment(app):
    student,manager,course,eid,lesson,rid=setup_pending(app)
    post(manager,'/admin',action='create_update',kind='announcement',title='Student welcome update',slug='welcome-update',
         summary='Welcome to LionTech',body='View your course details and complete payment.',publication_state='published',
         starts_at='',ends_at='',registration_url='',location='',publish_at='')
    page=student.get('/portal')
    for text in [b'Account automatically approved',b'Curriculum',b'View enrollment payment',
                 b'Student welcome update',b'Power BI']:
        assert text in page.data
    # Check the actual enrollment payment link, rather than a hidden translation key.
    from flask import url_for
    with app.test_request_context():
        payment_url=url_for('global_payment',enrollment_id=eid)
    assert ('href="'+payment_url+'"').encode() in page.data
    assert_locked(student,course,lesson,rid)
    with app.app_context():
        assert db.session.scalar(select(Student)).approved
        enrollment=db.session.get(Enrollment,eid)
        assert enrollment.status=='pending' and enrollment.payment_status=='unpaid'


def test_receipt_does_not_unlock_and_admin_combined_approval_does(app):
    student,manager,course,eid,lesson,rid=setup_pending(app)
    historical_payment(student,eid,'PAYMENT-TO-VERIFY')
    assert_locked(student,course,lesson,rid)
    assert b'Verify the enrollment payment' in post(manager,'/admin',action='approve',enrollment_id=eid).data
    assert student.post('/admin',data={'csrf':csrf(student,'/portal'),'action':'verify_and_approve','enrollment_id':eid}).status_code==302
    post(manager,'/admin',action='verify_and_approve',enrollment_id=eid)
    page=student.get('/portal')
    for text in [b'Enrollment confirmed',b'Register for live class',b'Access class materials',b'Join class']:
        assert text in page.data
    assert student.get(f'/class/{lesson}/join').location=='https://zoom.us/j/private-meeting'
    assert '186T95HhFghqKXATdxzXvvu43OWSmBFL2' in student.get(f'/course/{course}/materials').location
    assert student.get(f'/recording/{rid}/watch').location=='https://zoom.us/rec/private-recording'
    assert student.post(f'/class/{lesson}/register',data={'csrf':csrf(student,'/portal')},follow_redirects=True).status_code==200
    with app.app_context():
        en=db.session.get(Enrollment,eid)
        assert en.payment_status=='verified' and en.status=='approved'
        count=len(list(db.session.scalars(select(Notification))))
    post(manager,'/admin',action='verify_and_approve',enrollment_id=eid)
    with app.app_context():
        assert len(list(db.session.scalars(select(Notification))))==count


def test_approval_button_and_no_reference_guard(app):
    student,manager,course,eid,lesson,rid=setup_pending(app)
    page=manager.get('/admin?view=enrollments')
    assert b'Review \xc2\xb7 approve or decline' in page.data
    page=post(manager,'/admin',action='verify_and_approve',enrollment_id=eid)
    assert b'must submit a payment reference' in page.data
    with app.app_context():
        assert db.session.get(Enrollment,eid).payment_status=='unpaid'
    historical_payment(student,eid,'VERIFY-SEPARATELY')
    assert b'Review \xc2\xb7 approve or decline' in manager.get('/admin?view=enrollments').data
    post(manager,'/admin',action='verify_payment',enrollment_id=eid)
    assert b'Review \xc2\xb7 approve or decline' in manager.get('/admin?view=enrollments').data
    assert_locked(student,course,lesson,rid)
    post(manager,'/admin',action='approve',enrollment_id=eid)
    assert student.get(f'/class/{lesson}/join').status_code==302


def test_revocation_locks_course_and_schedule_but_keeps_account(app):
    student,manager,course,eid,lesson,rid=setup_pending(app)
    historical_payment(student,eid,'PAY-THEN-REVOKE')
    post(manager,'/admin',action='verify_and_approve',enrollment_id=eid)
    assert b'Paid course recording' in student.get('/portal').data
    post(manager,'/admin',action='reject',enrollment_id=eid)
    page=student.get('/portal')
    assert b'Paid Analytics Class' not in page.data
    assert b'Paid course recording' not in page.data
    assert_locked(student,course,lesson,rid)
    with app.app_context():
        assert db.session.scalar(select(Student)).approved


def test_each_course_has_its_own_access_gate(app):
    student,manager,course,eid,lesson,rid=setup_pending(app)
    historical_payment(student,eid,'ONE-COURSE-ONLY')
    post(manager,'/admin',action='verify_and_approve',enrollment_id=eid)
    with app.app_context():
        second=db.session.scalar(select(Course).where(Course.slug=='devops'))
        second_id=second.id
        cohort=db.session.scalar(select(Cohort.id).where(Cohort.course_id==second_id,Cohort.start_date==next_start_dates()[0]))
    request_enrollment(student,second_id,cohort)
    assert student.get(f'/course/{course}/materials').status_code==302
    assert student.get(f'/course/{second_id}/materials').status_code==403


def test_account_notification_saved_for_every_kind_and_backfilled_once(app):
    new,existing,manager=app.test_client(),app.test_client(),app.test_client()
    register(new,'new-request@example.com')
    register(existing,'old-request@example.com',kind='existing')
    # A duplicate signup never generates another support notification.
    register(new,'new-request@example.com')
    with app.app_context():
        assert len(list(db.session.scalars(select(AccountRequest))))==2
        legacy=Student(name='Legacy account',email='legacy-request@example.com',phone='+237600000002',
                       country='Cameroon',kind='existing',password_hash='unused',approved=False)
        db.session.add(legacy)
        db.session.commit()
        sid=legacy.id
        for _ in range(2):
            with db.engine.begin() as connection: seed(connection)
        db.session.expire_all()
        assert db.session.get(Student,sid).approved
        assert len(list(db.session.scalars(select(AccountRequest))))==3
        assert len(list(db.session.scalars(select(Enrollment))))==0
    admin(manager)
    page=manager.get('/admin?view=settings')
    assert b'Automatic WhatsApp delivery is not connected' in page.data
    assert b'new-request%40example.com' in page.data and b'old-request%40example.com' in page.data
    assert b'https://wa.me/14375756656' in page.data
    assert b'Legacy account' in page.data


def test_class_join_links_validate_and_unconfigured_links_do_not_leak_registration(app):
    student,manager,course,eid,lesson,rid=setup_pending(app)
    page=post(manager,'/admin',action='class_links',class_id=lesson,registration_url='https://zoom.us/register/updated',join_url='javascript:alert(1)')
    assert b'Enter a valid https or http link' in page.data
    with app.app_context():
        assert db.session.get(ClassSession,lesson).registration_url=='https://zoom.us/register/private-class'
    post(manager,'/admin',action='class_links',class_id=lesson,registration_url='https://zoom.us/register/private-class',join_url='')
    historical_payment(student,eid,'NO-JOIN-YET')
    post(manager,'/admin',action='verify_and_approve',enrollment_id=eid)
    assert student.get(f'/class/{lesson}/join').location.endswith('/portal#classes')
    assert b'Join class' not in student.get('/portal').data
