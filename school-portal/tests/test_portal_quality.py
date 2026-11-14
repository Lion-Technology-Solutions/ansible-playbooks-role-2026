"""Cross-feature journeys and UI/performance contracts for the portal."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit
from decimal import Decimal
from flask import url_for,template_rendered
from sqlalchemy import select
from app import db,Student,PaymentEntry,StaffTask,LessonTask,Lesson,Enrollment,ClassSession,PortalSessionActivity
from test_staff_portal import setup,create,login,post
from test_portal import admin,register,csrf


class PageInventory(HTMLParser):
    def __init__(self):super().__init__();self.ids=[];self.assets=[]
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if values.get('id'):self.ids.append(values['id'])
        if tag=='script' and values.get('src'):self.assets.append(values['src'])
        if tag=='link' and values.get('rel')=='stylesheet':self.assets.append(values.get('href',''))


def path_for(app,endpoint):
    with app.test_request_context():return url_for(endpoint)


def check_page(client,path,staff=False):
    response=client.get(path);assert response.status_code==200,(path,response.status_code,response.location)
    text=response.get_data(as_text=True);parser=PageInventory();parser.feed(text)
    assert len(parser.ids)==len(set(parser.ids)),(path,'Duplicate IDs')
    assert 'id="site-support"' in text and 'Skip to content' in text
    assert 'name="viewport"' in text
    assert len(response.data)<250000,(path,len(response.data))
    assert 'data-catalog-url=' in text
    if staff:
        assert 'staff-portal-page' in text
        assert len([a for a in parser.assets if urlsplit(a).path=='/static/staff.css'])==1
        assert len([a for a in parser.assets if urlsplit(a).path=='/static/staff.js'])==1
    return response


def test_public_ui_pages_are_small_and_have_support_and_accessible_navigation(app):
    client=app.test_client()
    for endpoint in ('home','program_catalog','updates','support_center','terms_of_service','privacy_policy','register','login','staff_login','admin_login'):
        check_page(client,path_for(app,endpoint))


def test_admin_screens_share_branding_and_review_workspace_assets(app):
    manager,_=setup(app)
    for view in ('dashboard','students','enrollments','payments','programs','classes','materials','announcements','reports','settings'):
        check_page(manager,'/admin?view='+view)
    for endpoint in ('staff_team','staff_workboard','admin_lesson_tasks','admin_lesson_submissions','admin_enrollment_submissions'):
        check_page(manager,path_for(app,endpoint),staff=True)
    for endpoint in ('schedule_admin','integration_hub','notification_admin','security_settings','admin_profile'):
        check_page(manager,path_for(app,endpoint))


def test_student_staff_instructor_workspaces_render_with_role_correct_assets(app):
    manager,sent=setup(app);student=app.test_client();register(student)
    for endpoint in ('portal','enrollments','live_classes','student_profile','security_account','connected_accounts','support_center'):
        check_page(student,path_for(app,endpoint))
    identity=create(app,'Quality Instructor',role='instructor');worker=login(app,sent,identity)
    for endpoint in ('staff_home','staff_tasks','staff_workboard','staff_lesson_tasks','staff_profile','staff_reports','staff_notifications','instructor_workspace'):
        check_page(worker,path_for(app,endpoint),staff=True)


def test_catalog_is_lazy_versioned_role_separated_and_contains_no_account_data(app):
    public=app.test_client();page=public.get('/student/login')
    assert len(page.data)<60000
    match=re.search(rb'id="portal-language-catalog" data-catalog-url="([^"]+)"',page.data);assert match
    public_url=match[1].decode().replace('&amp;','&');assert re.search(r'v=[0-9a-f]{16}',public_url)
    catalog=public.get(public_url);assert catalog.json['fr']['Password']=='Mot de passe'
    assert 'USD' not in catalog.json['fr']
    manager=app.test_client();admin(manager);page=manager.get('/admin')
    admin_url=re.search(rb'id="portal-language-catalog" data-catalog-url="([^"]+)"',page.data)[1].decode().replace('&amp;','&')
    assert admin_url!=public_url
    assert manager.get(admin_url).json['fr']['Password']=='Mot de passe'
    assert b'no-store' in catalog.headers['Cache-Control'].encode()


def test_ui_assets_are_versioned_cacheable_without_session_or_auth_refresh(app):
    manager,_=setup(app)
    page=manager.get('/admin');versioned=re.search(rb'src="([^\"]*languages\.js[^\"]*)"',page.data)[1].decode().replace('&amp;','&')
    assert re.search(r'v=[0-9a-f]{16}',versioned)
    with app.app_context():before=[(r.digest,r.last_active_at,r.revoked) for r in db.session.scalars(select(PortalSessionActivity))]
    response=manager.get(versioned)
    assert response.status_code==200 and 'immutable' in response.headers['Cache-Control']
    assert 'Set-Cookie' not in response.headers and 'Cookie' not in response.headers.get('Vary','')
    with app.app_context():assert [(r.digest,r.last_active_at,r.revoked) for r in db.session.scalars(select(PortalSessionActivity))]==before
    conditional=manager.get(versioned,headers={'If-None-Match':response.headers['ETag']});assert conditional.status_code==304
    assert 'no-store' in manager.get('/admin').headers['Cache-Control']
    assert 'must-revalidate' in manager.get('/static/languages.js').headers['Cache-Control']
    assert 'no-store' in manager.get('/static/missing-quality-check.css').headers['Cache-Control']


def test_signup_reconciled_enrollment_lesson_review_schedule_and_access_integrate(app):
    from test_lesson_tasks import package,submit,submission_id,decision
    from test_admin_enrollment import setup as enrollment_setup
    from test_admin_enrollment import review
    from test_portal import request_enrollment
    student,manager,sid,cid,cohort=enrollment_setup(app)
    manager,sent=setup(app);member=create(app,'Integrated Lesson Officer');worker=login(app,sent,member)
    with app.app_context():
        lesson=Lesson(course_id=cid,title='Integrated Linux lesson',body='Published original',published=True);db.session.add(lesson);db.session.commit();lid=lesson.id
    post(manager,'/admin/lesson-tasks',page='/admin/team',member_id=member,lesson_id=lid,action='content',due_on='2027-01-01')
    with app.app_context():tid=db.session.scalar(select(LessonTask.task_id))
    assert student.get('/lessons/'+str(lid)).status_code==403
    review(manager,sid,cid,cohort)
    assert student.get('/lessons/'+str(lid)).status_code==200
    files=package(app,worker,tid);submit(worker,tid);identity=submission_id(app)
    assert student.get('/files/'+files['outline']).status_code==403
    decision(manager,identity)
    assert student.get('/files/'+files['outline']).status_code==200
    assert b'Reviewed shell exercises' in student.get('/lessons/'+str(lid)).data
    path='/admin/lesson-tasks'
    post(manager,path,page=path,member_id=member,lesson_id=lid,action='schedule',due_on='2027-01-01')
    with app.app_context():schedule_tid=db.session.scalar(select(LessonTask.task_id).order_by(LessonTask.task_id.desc()))
    post(worker,f'/staff/lesson-tasks/{schedule_tid}/schedule',starts_at='2027-01-16T12:00',timezone='America/Toronto',duration='120',zoom_mode='manual',join_url='https://zoom.us/j/123456789',cohort_id=cohort)
    with app.app_context():class_id=db.session.scalar(select(ClassSession.id))
    classroom=student.get('/classes/'+str(class_id),follow_redirects=True)
    assert classroom.status_code==200 and b'Linux foundations updated' in classroom.data
    unpaid=app.test_client();register(unpaid,email='quality-unpaid@example.test');request_enrollment(unpaid,cid,cohort)
    assert unpaid.get('/lessons/'+str(lid)).status_code==403
    assert unpaid.get('/files/'+files['outline']).status_code==403
    assert unpaid.get('/classes/'+str(class_id)).status_code==403
    assert worker.get('/admin/lesson-submissions/'+identity).status_code==302
    balances=[]
    def capture_balance(sender,template,context,**unused):
        if template.name=='portal.html':balances.append(context['billing_for'](context['featured'])['remaining'])
    with template_rendered.connected_to(capture_balance,app):
        assert student.get('/portal').status_code==200
        assert balances[-1]==Decimal('600')
        with app.app_context():
            enrollment=db.session.scalar(select(Enrollment).where(Enrollment.student_id==sid,Enrollment.course_id==cid));db.session.add(PaymentEntry(enrollment_id=enrollment.id,reference='QUALITY-VERIFIED-100',amount=100,currency='USD'));db.session.commit()
        assert student.get('/portal').status_code==200
        assert balances[-1]==Decimal('500')
