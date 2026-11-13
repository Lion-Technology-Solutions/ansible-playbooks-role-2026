import re
from datetime import timedelta
from sqlalchemy import select
from app import db,Student,StaffMember,AuthMail,AuthProfile,ExtraProfileRole,MemberPerson,ProfileMembership,PortalSessionActivity,TeachingGrant,Course,now
from test_staff_portal import setup,create,login,post,PASSWORD
from test_portal import register,csrf,analytics


def admin_change(manager,anchor,action,**fields):
    return post(manager,'/admin/member-profiles',page='/admin/team',anchor=anchor,action=action,confirm_identity='yes',admin_password='test-admin-password',**fields)


def verify_switch(app,client,target):
    result=post(client,'/account/profiles',page='/account/profiles',profile=target)
    if b'Verify your profile switch' in result.data:
        with app.app_context():
            mail=db.session.scalar(select(AuthMail).order_by(AuthMail.created_at.desc()))
            body=app.extensions['integrations']['unseal']('auth_mail',mail.id,mail.ciphertext)['body']
        code=re.search(r'code is (\d{6})',body)[1]
        result=post(client,'/account/profiles/verify',page='/account/profiles/verify',code=code)
    if b'Activate your Student profile' in result.data:
        result=post(client,'/account/profiles/student-consent',page='/account/profiles/student-consent',accept_terms='yes')
    return result


def setup_person(app):
    manager,sent=setup(app);app.config['SECURITY_MAIL_PUBLISH']=lambda identity:None
    sid=create(app,'John');anchor='staff:'+str(sid)
    assert admin_change(manager,anchor,'create_student',country='Canada').status_code==200
    assert admin_change(manager,anchor,'roles',instructor='yes').status_code==200
    with app.app_context():
        student=db.session.scalar(select(Student).where(Student.email=='john@example.test'));student_id=student.id
    return manager,sent,sid,student_id


def test_real_switching_role_isolation_and_instructor_grants(app):
    manager,sent,sid,student_id=setup_person(app)
    with app.app_context():course=db.session.scalar(select(Course.id).where(Course.slug=='data-analytics'))
    assert post(manager,'/staff/teaching',page='/admin/team',member_id=sid,course_id=course,action='grant').status_code==200
    worker=login(app,sent,sid)
    with worker.session_transaction() as data:original_token=data['portal_session']
    with app.app_context():original_expiry=db.session.get(PortalSessionActivity,__import__('hashlib').sha256(original_token.encode()).hexdigest()).absolute_expires_at
    result=verify_switch(app,worker,'instructor:'+str(sid));assert result.status_code==200 and b'Lesson studio' in result.data
    assert worker.get('/teaching/workspace').status_code==200
    with worker.session_transaction() as data:assert not data.get('admin') and data['staff_id']==sid
    with app.app_context():assert db.session.get(StaffMember,sid).role=='staff'
    result=verify_switch(app,worker,'student:'+student_id)
    assert result.status_code==200,result.data[-1000:]
    with worker.session_transaction() as data:
        assert data['student_id']==student_id and not data.get('admin') and not data.get('staff_id')
        token=data['portal_session']
    with app.app_context():assert db.session.get(PortalSessionActivity,__import__('hashlib').sha256(token.encode()).hexdigest()).absolute_expires_at==original_expiry
    assert worker.get('/staff/workboard').status_code==403
    assert worker.get('/teaching/workspace').status_code==403
    assert worker.get('/admin').status_code==302
    assert b'John' in worker.get('/account/profile').data
    assert verify_switch(app,worker,'staff:'+str(sid)).status_code==200
    with worker.session_transaction() as data:assert data['staff_id']==sid and not data.get('student_id')
    outsider=create(app,'Other')
    assert post(worker,'/account/profiles',page='/account/profiles',profile='staff:'+str(outsider)).status_code==403
    assert admin_change(manager,'staff:'+str(sid),'roles').status_code==200
    assert worker.get('/staff').status_code==302
    assert worker.get('/teaching/workspace').status_code==403


def test_linked_source_revocation_and_idle_expiry(app):
    manager,sent,sid,student_id=setup_person(app);worker=login(app,sent,sid)
    assert verify_switch(app,worker,'student:'+student_id).status_code==200
    with app.app_context():row=db.session.get(StaffMember,sid);row.revision+=1;db.session.commit()
    result=worker.get('/account/profile');assert result.status_code==302 and 'profile-access-changed' in result.location
    worker=login(app,sent,sid)
    assert verify_switch(app,worker,'student:'+student_id).status_code==200
    app.config['SESSION_CLOCK']=lambda:now()+timedelta(minutes=15)
    assert worker.get('/account/profile').status_code==302
    with worker.session_transaction() as data:assert not data.get('student_id') and not data.get('staff_id')


def test_admin_identity_corrections_only_and_duplicate_usernames(app):
    manager,sent,sid,student_id=setup_person(app);worker=login(app,sent,sid)
    assert verify_switch(app,worker,'student:'+student_id).status_code==200
    response=post(worker,'/account/profile',page='/account/profile',name='Tampered',email='john@example.test')
    assert response.status_code==403
    assert post(worker,'/account/security',page='/account/security',action='username',username='tampered').status_code==403
    assert post(worker,'/admin/member-profiles',page='/account/profile',anchor='student:'+student_id,action='identity',name='Bad',confirm_identity='yes',admin_password=PASSWORD).status_code==403
    assert admin_change(manager,'student:'+student_id,'identity',name='John Updated',username='john_updated').status_code==200
    with app.app_context():
        assert db.session.get(Student,student_id).name=='John Updated'
        assert db.session.get(StaffMember,sid).name=='John Updated'
        assert db.session.get(AuthProfile,student_id).username=='john_updated'
    assert worker.get('/account/profile').status_code==302


def test_delegated_admin_uses_own_name_and_can_be_revoked(app):
    manager,sent,sid,student_id=setup_person(app)
    assert admin_change(manager,'staff:'+str(sid),'roles',administrator='yes',instructor='yes').status_code==200
    worker=login(app,sent,sid)
    result=verify_switch(app,worker,'administrator:'+str(sid))
    assert result.status_code==200 and b'John' in result.data
    with worker.session_transaction() as data:assert data.get('admin') and not data.get('staff_id') and not data.get('student_id')
    assert post(worker,'/staff/tasks',page='/admin',member_id=sid,title='Delegated administrator task',description='Audit identity',due_on='2027-01-01').status_code==200
    from app import StaffAudit
    with app.app_context():assert db.session.scalar(select(StaffAudit).where(StaffAudit.action=='task_assigned').order_by(StaffAudit.created_at.desc())).actor=='admin:staff:'+str(sid)
    assert post(worker,'/admin/profile',page='/admin',action='change_password',admin_password=PASSWORD,new_password='new-test-password',confirm_password='new-test-password').status_code==403
    assert admin_change(manager,'staff:'+str(sid),'roles',instructor='yes').status_code==200
    assert worker.get('/admin').status_code==302


def test_profile_contact_normalization_links_consent_and_unsafe_values(app):
    manager,sent=setup(app);sid=create(app,'Contact');worker=login(app,sent,sid)
    response=post(worker,'/account/contact',page='/account/contact',phone='+1 (437) 575-6656',whatsapp='+237 600000001',task_alerts='yes')
    assert response.status_code==200 and b'tel:+14375756656' in response.data and b'https://wa.me/237600000001' in response.data
    from app import MemberContact
    with app.app_context():
        row=db.session.get(MemberContact,'staff:'+str(sid));assert row.task_alerts and row.consented_at
    response=post(worker,'/account/contact',page='/account/contact',phone='javascript:alert(1)',whatsapp='bad')
    assert b'Use + and the country code' in response.data
    with app.app_context():assert db.session.get(MemberContact,'staff:'+str(sid)).phone=='+14375756656'
    assert app.test_client().get('/account/contact').status_code==403


def test_admin_assigns_staff_username_without_self_edit_and_duplicate_is_atomic(app):
    manager,sent=setup(app);sid=create(app,'Editable');other=create(app,'Another')
    assert admin_change(manager,'staff:'+str(sid),'identity',name='Corrected Name',username='corrected_user').status_code==200
    denied=admin_change(manager,'staff:'+str(other),'identity',name='Should roll back',username='corrected_user')
    assert denied.status_code==409
    with app.app_context():assert db.session.get(StaffMember,other).name=='Another'
    worker=app.test_client()
    result=post(worker,'/staff/sign-in',page='/staff/sign-in',identity='corrected_user',password=PASSWORD)
    assert b'code' in result.data.lower()
    code=re.search(r'code is (\d{6})',sent[-1]['body'])[1]
    assert post(worker,'/staff/verify',page='/staff/verify',code=code).status_code==200
    assert b'Corrected' in worker.get('/staff').data


def test_native_admin_switch_requires_destination_ownership_and_returns_to_own_identity(app):
    manager,sent=setup(app);app.config['SECURITY_MAIL_PUBLISH']=lambda identity:None
    sid=create(app,'John')
    assert admin_change(manager,'admin:1','link',target='staff:'+str(sid)).status_code==200
    assert admin_change(manager,'admin:1','identity',name='John').status_code==200
    assert post(manager,'/admin/profile-switch',page='/admin/profile-switch',role='student').status_code==200
    assert manager.get('/account/profiles').status_code==200
    with manager.session_transaction() as data:assert not data.get('role_preview')
    page=post(manager,'/account/profiles',page='/account/profiles',profile='staff:'+str(sid))
    assert b'Verify your profile switch' in page.data
    with manager.session_transaction() as data:assert data.get('admin') and not data.get('staff_id')
    with app.app_context():
        mail=db.session.scalar(select(AuthMail).order_by(AuthMail.created_at.desc()))
        body=app.extensions['integrations']['unseal']('auth_mail',mail.id,mail.ciphertext)['body']
    code=re.search(r'code is (\d{6})',body)[1]
    assert post(manager,'/account/profiles/verify',page='/account/profiles/verify',code='wrong').status_code==400
    assert post(manager,'/account/profiles/verify',page='/account/profiles/verify',code=code).status_code==200
    with manager.session_transaction() as data:assert not data.get('admin') and data.get('staff_id')==sid
    assert verify_switch(app,manager,'admin:1').status_code==200
    with manager.session_transaction() as data:assert data.get('admin') and not data.get('staff_id')
    assert b'John' in manager.get('/admin').data


def test_existing_student_accepts_own_linked_staff_invitation_without_signing_out(app):
    from legal_content import POLICY_VERSION
    manager,sent=setup(app);student=app.test_client();register(student)
    with app.app_context():student_id=db.session.scalar(select(Student.id))
    assert admin_change(manager,'student:'+student_id,'create_staff',department='Admissions').status_code==200
    invitation=next(item for item in sent if item['subject']=='You are invited to LionTech Academy staff')
    token=re.search(r'/staff/invitations/([^\s]+)',invitation['body'])[1];path='/staff/invitations/'+token
    assert student.get(path).status_code==200
    assert post(student,path,page=path,action='code').status_code==200
    code=re.search(r'code is (\d{6})',sent[-1]['body'])[1]
    result=post(student,path,page=path,code=code,password=PASSWORD,confirm_password=PASSWORD,policy_acknowledged='yes',policy_version=POLICY_VERSION)
    assert result.status_code==200
    with student.session_transaction() as data:assert data.get('staff_id') and not data.get('student_id') and data.get('portal_session')
    assert student.get('/staff').status_code==200


def test_separate_student_contact_numbers_apply_to_existing_directory_links(app):
    manager,sent=setup(app);student=app.test_client();register(student)
    with app.app_context():student_id=db.session.scalar(select(Student.id))
    assert post(student,'/account/contact',page='/account/contact',phone='+15555555551',whatsapp='+15555555552').status_code==200
    page=manager.get('/admin?view=students&student='+student_id)
    assert page.status_code==200 and b'wa.me/15555555552' in page.data and b'tel:+15555555551' in page.data
    assert b'wa.me/15555555551' not in page.data
    assert post(student,'/account/contact',page='/account/contact',phone='+15555555551',whatsapp='').status_code==200
    page=manager.get('/admin?view=students&student='+student_id)
    assert b'wa.me/15555555551' not in page.data and b'wa.me/15555555552' not in page.data
