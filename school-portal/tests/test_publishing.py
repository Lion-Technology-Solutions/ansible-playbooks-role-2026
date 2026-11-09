from datetime import timedelta
import re

from sqlalchemy import select

from app import db, Course, CoursePublication, PublicUpdate, Enrollment, seed, now
from test_portal import admin, analytics, approve, csrf, post, register, request_enrollment


def course_fields(**overrides):
    values = dict(action='create_course',name='Linux Administration',slug='linux-administration',
                  description='Practical Linux skills for the workplace.',skills='Linux · Bash',
                  duration='8 weeks',schedule='Saturday, 16:00 America/Toronto',
                  curriculum='Linux basics\nBash scripting',publication_state='draft')
    values.update(overrides)
    return values


def update_fields(**overrides):
    start=now()+timedelta(days=5)
    values=dict(action='create_update',kind='event',title='Linux Career Workshop',slug='linux-career-workshop',
                summary='Meet the LionTech instructors.',body='Learn about careers in Linux.\nBring your questions.',
                starts_at=start.strftime('%Y-%m-%dT%H:%M'),ends_at=(start+timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
                location='Online via Zoom',registration_url='https://zoom.us/example-registration',
                publish_at='',publication_state='draft')
    values.update(overrides)
    return values


def test_course_create_preview_publish_edit_and_unpublish(app):
    manager,visitor,student=app.test_client(),app.test_client(),app.test_client()
    admin(manager)
    post(manager,'/admin',**course_fields())
    with app.app_context():
        course=db.session.scalar(select(Course).where(Course.slug=='linux-administration'))
        course_id=course.id
        assert not course.publication.published
        assert course.profile.curriculum=='Linux basics\nBash scripting'
        assert course.publication.payment_links=={}
    assert visitor.get('/courses/linux-administration').status_code==404
    assert b'Linux Administration' not in visitor.get('/').data
    assert manager.get(f'/admin/courses/{course_id}/preview').status_code==200
    assert visitor.get(f'/admin/courses/{course_id}/preview').status_code==302
    register(student)
    assert b'Linux Administration' not in student.get('/enrollments').data
    assert b'not currently accepting' in request_enrollment(student,course_id).data
    post(manager,'/admin',**course_fields(action='edit_course',course_id=course_id,publication_state='published'))
    assert b'Linux Administration' in visitor.get('/').data
    details=visitor.get('/courses/linux-administration')
    assert details.status_code==200 and b'Bash scripting' in details.data
    assert b'Linux Administration' in student.get('/enrollments').data
    post(manager,'/admin',**course_fields(action='edit_course',course_id=course_id,name='Linux Operations',publication_state='published'))
    assert b'Linux Operations' in visitor.get('/courses/linux-administration').data
    post(manager,'/admin',**course_fields(action='edit_course',course_id=course_id,name='Linux Operations',publication_state='draft'))
    assert visitor.get('/courses/linux-administration').status_code==404
    with app.app_context():
        with db.engine.begin() as connection: seed(connection)
        db.session.expire_all()
        assert not db.session.get(CoursePublication,course_id).published


def test_courses_have_separate_payment_links_and_no_analytics_checkout(app):
    manager,student=app.test_client(),app.test_client()
    admin(manager)
    post(manager,'/admin',**course_fields(publication_state='published'))
    register(student)
    with app.app_context():
        c=db.session.scalar(select(Course).where(Course.slug=='linux-administration'))
        course_id=c.id
        from app import Cohort,next_start_dates
        cohort_id=db.session.scalar(select(Cohort.id).where(Cohort.course_id==course_id,Cohort.start_date==next_start_dates()[0]))
    page=request_enrollment(student,course_id,cohort_id)
    assert b'zx0r0urssqpb' not in page.data
    assert b'Arrange enrollment payment' in page.data
    post(manager,'/admin',action='program_profile',course_id=course_id,duration='8 weeks',schedule='',curriculum='Linux',
         tuition_0='Course price supplied by instructor',payment_link_0='https://flutterwave.com/pay/linux-course')
    assert b'https://flutterwave.com/pay/linux-course' in student.get('/enrollments').data
    with app.app_context():
        original=db.session.scalar(select(Course).where(Course.slug=='data-analytics'))
        assert original.publication.payment_links['Cameroon'].endswith('zx0r0urssqpb')


def test_unpublishing_preserves_current_students_access(app):
    manager,student,visitor=app.test_client(),app.test_client(),app.test_client()
    register(student)
    admin(manager)
    course,cohort=analytics(app)
    eid=approve(app,student,manager,course,cohort)
    post(manager,'/admin',action='edit_course',course_id=course,name='Data Analytics',description='Practical analytics',
         skills='Excel · SQL',publication_state='draft')
    assert visitor.get('/courses/data-analytics').status_code==404
    assert b'Data Analytics' not in visitor.get('/').data
    page=student.get('/portal')
    assert page.status_code==200 and b'yBAAj2k5TM2lEqpMXZY3ZQ' in page.data
    with app.app_context():
        assert db.session.get(Enrollment,eid).status=='approved'


def test_event_draft_scheduling_publication_and_unpublication(app,monkeypatch):
    import app as portal_module
    manager,visitor=app.test_client(),app.test_client()
    admin(manager)
    post(manager,'/admin',**update_fields())
    with app.app_context():
        event=db.session.scalar(select(PublicUpdate))
        event_id=event.id
    assert visitor.get('/updates/linux-career-workshop').status_code==404
    assert b'Linux Career Workshop' not in visitor.get('/updates').data
    assert manager.get(f'/admin/updates/{event_id}/preview').status_code==200
    assert visitor.get(f'/admin/updates/{event_id}/preview').status_code==302
    future=now()+timedelta(days=1)
    post(manager,'/admin',**update_fields(action='edit_update',update_id=event_id,publication_state='published',publish_at=future.strftime('%Y-%m-%dT%H:%M')))
    assert visitor.get('/updates/linux-career-workshop').status_code==404
    monkeypatch.setattr(portal_module,'now',lambda:future+timedelta(minutes=1))
    page=visitor.get('/updates/linux-career-workshop')
    assert page.status_code==200 and b'Online via Zoom' in page.data
    assert b'https://zoom.us/example-registration' in page.data
    assert b'Linux Career Workshop' in visitor.get('/').data
    assert b'Linux Career Workshop' in visitor.get('/updates').data
    post(manager,'/admin',**update_fields(action='edit_update',update_id=event_id,publication_state='draft'))
    assert visitor.get('/updates/linux-career-workshop').status_code==404
    assert b'Linux Career Workshop' not in visitor.get('/').data


def test_public_announcements_escape_text_and_validate_events(app):
    manager,visitor=app.test_client(),app.test_client()
    admin(manager)
    page=post(manager,'/admin',**update_fields(starts_at=''))
    assert b'Events need a start time' in page.data
    page=post(manager,'/admin',**update_fields(ends_at=(now()-timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')))
    assert b'Events need a start time' in page.data
    page=post(manager,'/admin',**update_fields(registration_url='javascript:alert(1)'))
    assert b'Enter a valid https or http link' in page.data
    post(manager,'/admin',**update_fields(kind='announcement',title='Academy Update',slug='academy-update',
         body='<script>alert(1)</script>\nNew courses coming soon.',publication_state='published'))
    page=visitor.get('/updates/academy-update')
    assert page.status_code==200 and b'&lt;script&gt;' in page.data and b'<script>alert' not in page.data
    assert b'Academy Update' in visitor.get('/').data
    assert b'Academy Update' in visitor.get('/updates').data
    page=post(manager,'/admin',**update_fields(kind='announcement',slug='academy-update'))
    assert b'already exists' in page.data
    with app.app_context():
        assert len(list(db.session.scalars(select(PublicUpdate))))==1


def test_only_admin_can_publish_and_csrf_and_slug_collisions_are_enforced(app):
    visitor,student,manager=app.test_client(),app.test_client(),app.test_client()
    token=csrf(visitor)
    assert visitor.post('/admin',data={'csrf':token,**course_fields(publication_state='published')}).status_code==302
    register(student)
    assert student.post('/admin',data={'csrf':csrf(student,'/enrollments'),**course_fields(publication_state='published')}).status_code==302
    admin(manager)
    assert manager.post('/admin',data=course_fields(publication_state='published')).status_code==400
    post(manager,'/admin',**course_fields(publication_state='published'))
    page=post(manager,'/admin',**course_fields())
    assert b'already exists' in page.data
    page=post(manager,'/admin',**course_fields(slug='invalid',publication_state='invented'))
    assert b'Choose Save draft or Publish course' in page.data
    with app.app_context():
        assert len(list(db.session.scalars(select(Course).where(Course.slug=='linux-administration'))))==1
        assert db.session.scalar(select(Course).where(Course.slug=='invalid')) is None
