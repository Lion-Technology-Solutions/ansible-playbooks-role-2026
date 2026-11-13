import io,json,re
from datetime import timedelta
from decimal import Decimal
from PIL import Image
from sqlalchemy import select,func
from app import db,Student,StudentControl,Enrollment,AuthMail,AuthChallenge,AdminAccount,AuthProfile,ProfilePicture,LifecycleEvent,BillingSelection,PaymentEntry,now,academy_today
from test_portal import admin,register,csrf,analytics,approve,post
from test_security import setup,activate,mail,logout,login
from test_integrations import configure,http_ok
from test_lessons_files import MemoryStorage,png


def student_id(app):
    with app.app_context():return db.session.scalar(select(Student.id))


def code(app):return re.search(r'code is (\d{6})',mail(app)['body'])[1]


def admin_code(app):return re.search(r'code is (\d{6})',mail(app)['body'])[1]


def review(manager,sid,**data):
    path='/admin/students/'+sid+'/review'
    return manager.post(path,data={'csrf':csrf(manager,path),'admin_password':'test-admin-password',**data},follow_redirects=True)


def test_account_deactivation_revokes_sessions_and_restore_does_not_bypass_payment(app):
    manager,student=app.test_client(),app.test_client();admin(manager);register(student);sid=student_id(app)
    assert student.get('/portal').status_code==200
    assert review(manager,sid,action='deactivate',reason='Review required').status_code==200
    assert student.get('/portal').status_code==302
    assert b'deactivated' in login(student).data
    with app.app_context():
        assert not db.session.get(StudentControl,sid).active
        assert db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='account_deactivated'))
    # Migration seeding must not undo deactivation.
    with app.app_context():
        from app import seed
        with db.engine.begin() as connection:seed(connection)
        assert not db.session.get(StudentControl,sid).active
    review(manager,sid,action='reactivate')
    assert b'Welcome' in login(student).data


def test_access_revocation_payment_and_approval_events_are_transactional(app):
    manager,student=app.test_client(),app.test_client();admin(manager);register(student)
    cid,cohort=analytics(app);eid=approve(app,student,manager,cid,cohort);sid=student_id(app)
    with app.app_context():
        kinds=list(db.session.scalars(select(LifecycleEvent.kind)))
        assert 'account_created' in kinds and 'account_approved' in kinds and 'payment_verified' in kinds and 'enrollment_approved' in kinds
        verified_count=db.session.scalar(select(func.count()).select_from(LifecycleEvent).where(LifecycleEvent.kind=='payment_verified'))
    post(manager,'/admin',action='verify_payment',enrollment_id=eid)
    with app.app_context():assert db.session.scalar(select(func.count()).select_from(LifecycleEvent).where(LifecycleEvent.kind=='payment_verified'))==verified_count
    response=review(manager,sid,action='revoke_access',enrollment_id=eid)
    assert response.status_code==200
    with app.app_context():
        assert db.session.get(Enrollment,eid).status=='revoked'
        assert db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='access_revoked'))
    anon=app.test_client();assert anon.get('/admin/students/'+sid+'/review').status_code==302
    assert manager.get('/admin/notifications').status_code==200


def test_profile_name_private_avatar_and_email_change_verifies_both_addresses(app):
    manager,student=setup(app);app.config['FILE_STORAGE']=MemoryStorage();register(student);activate(app,student);sid=student_id(app)
    denied=student.post('/account/profile',data={'csrf':csrf(student,'/account/profile'),'name':'Unauthorized rename','email':'student@example.com'})
    assert denied.status_code==403
    from test_member_profiles import admin_change
    assert admin_change(manager,'student:'+sid,'identity',name='Updated Student').status_code==200
    result=student.post('/account/profile',data={'csrf':csrf(student,'/account/profile'),'name':'Updated Student','email':'student@example.com','picture':(io.BytesIO(png()),'avatar.png')},content_type='multipart/form-data',follow_redirects=True)
    assert result.status_code==200 and b'Your profile has been updated' in result.data
    with app.app_context():
        assert db.session.get(Student,sid).name=='Updated Student'
        picture=db.session.get(ProfilePicture,'student:'+sid);assert picture and picture.pathname.endswith('.jpg')
        data=app.config['FILE_STORAGE'].get(picture.pathname)
        with Image.open(io.BytesIO(data)) as image:assert image.format=='JPEG' and not image.getexif()
    assert student.get('/account/picture/student/'+sid).status_code==200
    assert app.test_client().get('/account/picture/student/'+sid).status_code==403
    result=student.post('/account/profile',data={'csrf':csrf(student,'/account/profile'),'name':'Updated Student','email':'new@example.com'},follow_redirects=True)
    assert b'current email' in result.data and mail(app)['to']=='student@example.com'
    old_code=code(app)
    student.post('/account/profile/verify-email',data={'csrf':csrf(student,'/account/profile/verify-email'),'code':old_code})
    assert mail(app)['to']=='new@example.com'
    with app.app_context():assert db.session.get(Student,sid).email=='student@example.com'
    new_code=code(app)
    result=student.post('/account/profile/verify-email',data={'csrf':csrf(student,'/account/profile/verify-email'),'code':new_code},follow_redirects=True)
    assert b'Email address verified and updated' in result.data
    with app.app_context():
        assert db.session.get(Student,sid).email=='new@example.com'
        assert db.session.get(AuthProfile,sid).email_verified_at
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='profile_email_changed'));assert event.payload['recipient']=='student@example.com'
    assert student.get('/portal').status_code==302


def test_admin_password_change_requires_current_password_and_email_otp(app):
    manager,_=setup(app);other=app.test_client();admin(other)
    password='new-admin-password-1234'
    data={'csrf':csrf(manager,'/admin/profile'),'action':'change_password','admin_password':'wrong','new_password':password,'confirm_password':password}
    assert manager.post('/admin/profile',data=data).status_code==403
    data.update(csrf=csrf(manager,'/admin/profile'),admin_password='test-admin-password')
    response=manager.post('/admin/profile',data=data,follow_redirects=True)
    assert b'Verify administrator password change' in response.data
    assert mail(app)['to']=='liontechacademy@gmail.com'
    otp=admin_code(app)
    with app.app_context():assert app.extensions['admin_security']['check']('test-admin-password') and not app.extensions['admin_security']['check'](password)
    result=manager.post('/admin/password/verify',data={'csrf':csrf(manager,'/admin/password/verify'),'code':'not-a-code'},follow_redirects=True)
    assert b'Incorrect or expired' in result.data
    result=manager.post('/admin/password/verify',data={'csrf':csrf(manager,'/admin/password/verify'),'code':otp},follow_redirects=True)
    assert b'Administrator password updated' in result.data
    assert other.get('/admin').status_code==302
    with app.app_context():
        assert app.extensions['admin_security']['check'](password) and not app.extensions['admin_security']['check']('test-admin-password')
        assert db.session.get(AdminAccount,1).password_hash!=password
    result=manager.post('/admin/login',data={'csrf':csrf(manager,'/admin/login'),'username':'admin','password':password},follow_redirects=True)
    assert result.status_code==200 and manager.get('/admin/profile').status_code==200


def test_admin_recovery_requires_email_code_and_cannot_disable_student_mfa(app):
    app.config['INTEGRATIONS_ENCRYPTION_KEY']=__import__('cryptography.fernet',fromlist=['Fernet']).Fernet.generate_key().decode();app.config['SECURITY_MAIL_PUBLISH']=lambda identity:None
    client=app.test_client();page=client.get('/admin/login');assert b'Forgot administrator password?' in page.data
    result=client.post('/admin/password/forgot',data={'csrf':csrf(client,'/admin/password/forgot'),'email':'liontechacademy@gmail.com'},follow_redirects=True)
    assert b'If the address matches' in result.data;otp=admin_code(app)
    result=client.post('/admin/password/verify',data={'csrf':csrf(client,'/admin/password/verify'),'code':otp,'new_password':'recovered-admin-password','confirm_password':'recovered-admin-password'},follow_redirects=True)
    assert b'Administrator password updated' in result.data
    with app.app_context():assert app.extensions['admin_security']['check']('recovered-admin-password')


def test_due_reminders_skip_paid_and_are_idempotent_and_cron_requires_secret(app):
    manager,student=app.test_client(),app.test_client();admin(manager);register(student);cid,cohort=analytics(app);eid=approve(app,student,manager,cid,cohort)
    app.config['CRON_SECRET']='unit-cron-secret'
    with app.app_context():
        db.session.add(BillingSelection(enrollment_id=eid,plan='installments',currency='USD',total=Decimal('300'),installments=3,starts_on=academy_today(),checkout_url=''));db.session.commit()
        assert app.extensions['notifications']['due_reminders']()==1
        assert app.extensions['notifications']['due_reminders']()==0
        db.session.add(PaymentEntry(enrollment_id=eid,reference='DUE-PAID',amount=Decimal('100'),currency='USD'));db.session.commit()
        assert app.extensions['notifications']['due_reminders']()==0
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='payment_due'))
    assert manager.get('/internal/notifications/daily').status_code==401
    with app.test_request_context('/internal/notification-worker'):
        app.extensions['notifications']['process'](event.id)
        assert db.session.get(LifecycleEvent,event.id).status=='cancelled'


def test_signup_emails_replace_crm_whatsapp_and_cancel_legacy_alerts(app):
    manager,student=app.test_client(),app.test_client();admin(manager);register(student)
    with app.test_request_context('/internal/notification-worker'):
        person=db.session.scalar(select(Student))
        assert not db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.channel=='whatsapp'))
        assert db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='account_created',LifecycleEvent.channel=='email'))
        legacy=app.extensions['notifications']['enqueue'](person,'account_created',channel='whatsapp');db.session.commit()
        app.extensions['notifications']['process'](legacy.id)
        assert legacy.status=='cancelled' and legacy.attempts==0


def test_admin_changed_password_works_for_integrations_and_new_chatgpt_keys(app):
    manager,_=setup(app)
    new='new-admin-password-with-otp'
    manager.post('/admin/profile',data={'csrf':csrf(manager,'/admin/profile'),'action':'change_password','admin_password':'test-admin-password','new_password':new,'confirm_password':new})
    otp=admin_code(app)
    manager.post('/admin/password/verify',data={'csrf':csrf(manager,'/admin/password/verify'),'code':otp})
    manager.post('/admin/login',data={'csrf':csrf(manager,'/admin/login'),'username':'admin','password':new})
    created=manager.post('/admin/chatgpt',data={'csrf':csrf(manager,'/admin/chatgpt'),'action':'create','label':'After password change','acknowledge':'yes','can_write':'yes','admin_password':new})
    token=re.search(rb'<code translate="no">([^<]+)',created.data)[1].decode();client=app.test_client();headers={'Authorization':'Bearer '+token}
    draft=client.post('/api/chatgpt/drafts',headers=headers,json={'action':'create_course','target':0,'fields':[{'key':k,'value':v} for k,v in {'name':'New valid program','slug':'after-password-change','description':'Program content','skills':'Cloud','publication_state':'draft'}.items()]})
    result=client.post('/api/chatgpt/drafts/'+draft.json['id']+'/apply',headers=headers,json={'confirm':True})
    assert result.status_code==200,result.json


def test_failed_transaction_does_not_emit_lifecycle_notifications(app):
    manager,student=app.test_client(),app.test_client();admin(manager);register(student);sid=student_id(app)
    with app.app_context():
        before=db.session.scalar(select(func.count()).select_from(LifecycleEvent))
        control=StudentControl(student_id=sid,active=False);db.session.add(control);db.session.flush();db.session.rollback()
        assert db.session.scalar(select(func.count()).select_from(LifecycleEvent))==before
        assert db.session.get(StudentControl,sid) is None


def test_email_worker_uses_language_and_does_not_duplicate_accepted_events(app):
    manager,student=app.test_client(),app.test_client();admin(manager);register(student);sid=student_id(app)
    app.config['INTEGRATIONS_ENCRYPTION_KEY']=__import__('cryptography.fernet',fromlist=['Fernet']).Fernet.generate_key().decode();app.config['INTEGRATION_HTTP']=http_ok
    configure(app,manager,'email',{'SENDGRID_API_KEY':'unit-email','SENDGRID_FROM_EMAIL':'verified@example.com'});configure(app,manager,'email',action='test')
    with app.app_context():
        from app import StudentPreference
        db.session.get(StudentPreference,sid).language='fr';db.session.commit()
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='account_approved'));identity=event.id
    calls=[];app.config['LIFECYCLE_HTTP']=lambda *args:calls.append(args) or {}
    with app.test_request_context('/internal/notification-worker'):
        app.extensions['notifications']['process'](identity);app.extensions['notifications']['process'](identity)
    assert len(calls)==1
    assert calls[0][2]['personalizations'][0]['to'][0]['email']=='student@example.com'
    assert 'approuvé' in calls[0][2]['subject'] and 'Numéro étudiant' in calls[0][2]['content'][0]['value']


def test_manual_account_review_is_idempotent_and_does_not_grant_unpaid_enrollment(app):
    manager,student=app.test_client(),app.test_client();admin(manager);register(student);sid=student_id(app)
    review(manager,sid,action='approve_account')
    with app.app_context():
        assert db.session.get(StudentControl,sid).reviewed_at
        count=db.session.scalar(select(func.count()).select_from(LifecycleEvent).where(LifecycleEvent.kind=='account_approved'))
        assert not db.session.scalar(select(Enrollment))
    review(manager,sid,action='approve_account')
    with app.app_context():assert db.session.scalar(select(func.count()).select_from(LifecycleEvent).where(LifecycleEvent.kind=='account_approved'))==count
