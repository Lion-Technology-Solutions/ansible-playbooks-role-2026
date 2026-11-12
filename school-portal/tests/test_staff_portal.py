import io,re
from datetime import timedelta,date
from cryptography.fernet import Fernet
from sqlalchemy import select
from werkzeug.security import generate_password_hash
from app import db,StaffMember,StaffInvite,StaffChallenge,StaffPrivateData,StaffDocument,StaffReport,StaffReportVersion,StaffReview,StaffTask,StaffGrant,StaffMail,StaffAudit,now
from test_portal import admin,csrf,register
from test_lessons_files import MemoryStorage

PASSWORD='staff-test-password'


def setup(app):
    sent=[];app.config.update(INTEGRATIONS_ENCRYPTION_KEY=Fernet.generate_key().decode(),STAFF_MAIL_SENDER=lambda payload:sent.append(payload),FILE_STORAGE=MemoryStorage())
    manager=app.test_client();admin(manager)
    return manager,sent


def create(app,name='Staff',role='staff',supervisor=None,scope='assigned',department='Admissions'):
    with app.app_context():
        member=StaffMember(email=name.lower().replace(' ','')+'@example.test',name=name,department=department,role=role,scope=scope,supervisor_id=supervisor,password_hash=generate_password_hash(PASSWORD,method='pbkdf2:sha256:600000'),active=True,verified_at=now());db.session.add(member);db.session.commit();return member.id


def login(app,sent,identity):
    with app.app_context():email=db.session.get(StaffMember,identity).email
    client=app.test_client();result=client.post('/staff/sign-in',data={'csrf':csrf(client,'/staff/sign-in'),'identity':email,'password':PASSWORD})
    assert result.status_code==302 and '/staff/verify' in result.location
    code=re.search(r'code is (\d{6})',sent[-1]['body'])[1]
    result=client.post('/staff/verify',data={'csrf':csrf(client,'/staff/verify'),'code':code},follow_redirects=True)
    assert result.status_code==200,result.data[-1500:]
    return client


def post(client,path,fields=None,page='/staff',**extra):
    data={'csrf':csrf(client,page),**(fields or {}),**extra}
    return client.post(path,data=data,follow_redirects=True)


def test_invitation_bound_email_role_and_single_use(app):
    manager,sent=setup(app)
    result=post(manager,'/admin/team',page='/admin/team',email='invited@example.test',name='Invited Colleague',department='Operations',role='staff',scope='assigned',admin_password='test-admin-password')
    assert result.status_code==200 and b'Invited Colleague' in result.data
    token=re.search(r'/staff/invitations/([^\s]+)',sent[-1]['body'])[1]
    client=app.test_client();path='/staff/invitations/'+token
    assert client.get(path).status_code==200
    post(client,path,page=path,action='code');code=re.search(r'code is (\d{6})',sent[-1]['body'])[1]
    from legal_content import POLICY_VERSION
    result=post(client,path,page=path,code=code,password=PASSWORD,confirm_password=PASSWORD,policy_acknowledged='yes',policy_version=POLICY_VERSION,role='payroll')
    assert result.status_code==200 and b'Welcome' in result.data
    with app.app_context():
        member=db.session.scalar(select(StaffMember));assert member.role=='staff' and member.active and member.verified_at
        assert db.session.scalar(select(StaffInvite)).used
    assert app.test_client().get(path).status_code==410
    assert client.post('/admin/team',data={'csrf':csrf(client,'/staff'),'email':'another@example.test'}).status_code==302
    with app.app_context():assert db.session.scalar(select(db.func.count()).select_from(StaffMember))==1


def test_invite_resend_revoke_expiry_and_staff_login_require_verification(app):
    manager,sent=setup(app)
    post(manager,'/admin/team',page='/admin/team',email='pending@example.test',name='Pending',department='Teaching',role='instructor',scope='assigned',admin_password='test-admin-password')
    first=re.search(r'/staff/invitations/([^\s]+)',sent[-1]['body'])[1]
    with app.app_context():sid=db.session.scalar(select(StaffMember.id))
    post(manager,f'/admin/team/{sid}/manage',page='/admin/team',action='resend',admin_password='test-admin-password')
    second=re.search(r'/staff/invitations/([^\s]+)',sent[-1]['body'])[1]
    assert first!=second and app.test_client().get('/staff/invitations/'+first).status_code==410
    post(manager,f'/admin/team/{sid}/manage',page='/admin/team',action='revoke',admin_password='test-admin-password')
    assert app.test_client().get('/staff/invitations/'+second).status_code==410
    assert b'Revoked' in manager.get('/admin/team').data
    with app.app_context():
        pending=db.session.get(StaffMember,sid);pending.password_hash=generate_password_hash(PASSWORD);db.session.commit()
    client=app.test_client();result=post(client,'/staff/sign-in',page='/staff/sign-in',identity='pending@example.test',password=PASSWORD)
    assert b'Staff sign in' in result.data
    assert client.get('/staff').status_code==302


def test_session_revoke_scope_separation_and_idle_expiry(app):
    manager,sent=setup(app);sid=create(app);client=login(app,sent,sid)
    with client.session_transaction() as state:assert state.get('staff_id')==sid and not state.get('admin') and not state.get('student_id')
    assert client.get('/admin/team').status_code==302
    assert client.get('/staff/people').status_code==403
    post(manager,f'/admin/team/{sid}/manage',page='/admin/team',action='suspend',admin_password='test-admin-password')
    assert client.get('/staff').status_code==302
    post(manager,f'/admin/team/{sid}/manage',page='/admin/team',action='reactivate',admin_password='test-admin-password')
    client=login(app,sent,sid)
    app.config['SESSION_CLOCK']=lambda:now()+timedelta(minutes=15)
    assert client.get('/staff').status_code==302
    assert '/staff/sign-in' in client.get('/session/status').json.get('login_url','') or client.get('/staff').status_code==302


def test_scoped_tasks_reports_versions_pdf_and_supervisor_email_copies(app):
    manager,sent=setup(app);sup=create(app,'Supervisor','supervisor');sid=create(app,'Employee',supervisor=sup);outsider=create(app,'Outsider','supervisor')
    worker=login(app,sent,sid);supervisor=login(app,sent,sup);other=login(app,sent,outsider)
    assert post(supervisor,'/staff/tasks',member_id=sid,title='Follow up with leads',description='Record outcomes',due_on=now().date().isoformat()).status_code==200
    with app.app_context():task_id=db.session.scalar(select(StaffTask.id))
    assert post(worker,f'/staff/tasks/{task_id}',status='completed',result='Completed 18 follow-ups.').status_code==200
    assert post(other,f'/staff/tasks/{task_id}',status='completed',result='Tampered').status_code==403
    today=(now()-timedelta(days=1)).date().isoformat()
    report=post(worker,'/staff/reports',action='submit',report_date=today,completed='Updated lead records',outcomes='18 contacted',pending='5 follow-ups',blockers='None',priorities='Next intake')
    assert report.status_code==200
    with app.app_context():
        row=db.session.scalar(select(StaffReport));rid=row.id;version=db.session.scalar(select(StaffReportVersion));vid=version.id;original=dict(version.payload)
        assert row.latest==1 and row.status=='submitted'
        mails=list(db.session.scalars(select(StaffMail).where(StaffMail.kind=='report')));assert len(mails)==2 and all(m.status=='sent' for m in mails)
    copies=[p for p in sent if p['subject'].startswith('Daily staff report')]
    assert {p['to'] for p in copies}=={'employee@example.test','supervisor@example.test'} and all(p['attachments'][0]['data'].startswith(b'%PDF-') for p in copies)
    assert other.get(f'/staff/reports/{rid}').status_code==403
    assert other.get(f'/staff/reports/versions/{vid}.pdf').status_code==403
    assert supervisor.get(f'/staff/reports/versions/{vid}.pdf').data.startswith(b'%PDF-')
    assert post(worker,f'/staff/reports/{rid}',decision='reviewed',comment='Self approve',version_id=vid).status_code==403
    assert post(supervisor,f'/staff/reports/{rid}',decision='revision_requested',comment='Add the outcome detail.',version_id=vid).status_code==200
    assert post(worker,'/staff/reports',action='submit',report_date=today,completed='Updated lead records',outcomes='18 contacted, 3 enrollments').status_code==200
    with app.app_context():
        assert db.session.get(StaffReportVersion,vid).payload==original
        assert db.session.get(StaffReport,rid).latest==2
        new=db.session.scalar(select(StaffReportVersion).where(StaffReportVersion.version==2));newid=new.id
    assert post(supervisor,f'/staff/reports/{rid}',decision='reviewed',comment='Stale review',version_id=vid).status_code==409
    assert post(supervisor,f'/staff/reports/{rid}',decision='reviewed',comment='Reviewed outcome.',version_id=newid).status_code==200
    assert post(worker,'/staff/reports',action='submit',report_date=today,completed='Rewrite',outcomes='Rewrite').status_code==409


def test_sensitive_data_encrypted_and_hr_payroll_are_separate(app):
    manager,sent=setup(app);sup=create(app,'Supervisor','supervisor');sid=create(app,'Employee',supervisor=sup);hrid=create(app,'Human Resources','hr',scope='organization');payid=create(app,'Payroll','payroll',scope='organization')
    worker=login(app,sent,sid);hr=login(app,sent,hrid);pay=login(app,sent,payid);supervisor=login(app,sent,sup)
    assert post(worker,'/staff/profile',kind='personal',phone='+14375550111',address='123 Confidential Street',emergency_contact='Contact',action='submit').status_code==200
    assert post(worker,'/staff/profile',kind='payroll',country='Canada',holder='Employee',bank='Example Bank',institution='001',transit='12345',account='987654321',action='submit').status_code==200
    with app.app_context():
        row=db.session.get(StaffPrivateData,(sid,'payroll'));assert '987654321' not in row.ciphertext and row.status=='submitted'
    assert supervisor.get(f'/staff/personnel/{sid}/personal').status_code==403
    assert supervisor.get(f'/staff/personnel/{sid}/payroll').status_code==403
    assert manager.get(f'/staff/personnel/{sid}/payroll').status_code!=200
    assert hr.get(f'/staff/personnel/{sid}/personal').status_code==200
    assert hr.get(f'/staff/personnel/{sid}/payroll').status_code==403
    assert pay.get(f'/staff/personnel/{sid}/personal').status_code==403
    assert b'987654321' in pay.get(f'/staff/personnel/{sid}/payroll').data
    with worker.session_transaction() as state:state['staff_stepup_until']=0
    page=worker.get('/staff/profile?tab=payroll');assert b'987654321' not in page.data and b'Verify for restricted access' in page.data
    assert post(worker,'/staff/profile',kind='payroll',country='Canada',holder='Employee',bank='Bank',institution='001',transit='12345',account='123').status_code==403
    assert not any('987654321' in p['body'] for p in sent)


def test_documents_private_permissions_and_no_admin_automatic_access(app):
    manager,sent=setup(app);sid=create(app,'Employee');hrid=create(app,'HR','hr',scope='organization');payid=create(app,'Payroll','payroll',scope='organization');outsider=create(app,'Other')
    worker=login(app,sent,sid);hr=login(app,sent,hrid);pay=login(app,sent,payid);other=login(app,sent,outsider)
    result=post(worker,'/staff/documents',kind='government_id',file=(io.BytesIO(b'%PDF-1.7\nprivate ID'),'id.pdf'))
    assert result.status_code==200
    with app.app_context():doc=db.session.scalar(select(StaffDocument));identity=doc.id;path=doc.pathname
    assert b'private ID' not in app.config['FILE_STORAGE'].get(path)
    assert hr.get('/staff/documents/'+identity).data.endswith(b'private ID')
    for client in (manager,pay,other,app.test_client()):assert client.get('/staff/documents/'+identity).status_code==403
    with hr.session_transaction() as state:state['staff_stepup_until']=0
    locked=hr.get('/staff/documents/'+identity);assert locked.status_code==403 and b'Verify for restricted access' in locked.data
    assert post(hr,'/staff/secure-access',password=PASSWORD,return_to='/staff/documents/'+identity).status_code==200
    code=re.search(r'code is (\d{6})',sent[-1]['body'])[1]
    assert post(hr,'/staff/verify',page='/staff/verify',code=code).data.endswith(b'private ID')


def test_mail_failure_keeps_report_and_retry_is_authorized(app):
    manager,sent=setup(app);sid=create(app);worker=login(app,sent,sid)
    app.config['STAFF_MAIL_SENDER']=lambda payload:(_ for _ in ()).throw(RuntimeError('offline'))
    result=post(worker,'/staff/reports',action='submit',report_date=(now()-timedelta(days=1)).date().isoformat(),completed='Completed tasks',outcomes='Outcomes')
    assert result.status_code==200
    with app.app_context():
        report=db.session.scalar(select(StaffReport));assert report.status=='submitted'
        row=db.session.scalar(select(StaffMail).where(StaffMail.kind=='report',StaffMail.member_id==sid));mid=row.id;assert row.status=='failed' and row.ciphertext
    assert app.test_client().post('/staff/mail/'+mid+'/retry').status_code in (400,403)
    app.config['STAFF_MAIL_SENDER']=lambda payload:sent.append(payload)
    assert post(worker,'/staff/mail/'+mid+'/retry').status_code==200
    with app.app_context():assert db.session.get(StaffMail,mid).status=='sent'


def test_csrf_otp_wrong_attempts_and_recovery_session_revocation(app):
    manager,sent=setup(app);sid=create(app)
    client=app.test_client();assert client.post('/staff/sign-in',data={'identity':'staff@example.test','password':PASSWORD}).status_code==400
    client.post('/staff/sign-in',data={'csrf':csrf(client,'/staff/sign-in'),'identity':'staff@example.test','password':PASSWORD})
    actual=re.search(r'code is (\d{6})',sent[-1]['body'])[1]
    wrong='000001' if actual=='000000' else '000000'
    for _ in range(5):assert post(client,'/staff/verify',page='/staff/verify',code=wrong).status_code==400
    correct=re.search(r'code is (\d{6})',sent[-1]['body'])[1]
    assert post(client,'/staff/verify',page='/staff/verify',code=correct).status_code==400
    client=login(app,sent,sid);recovery=app.test_client()
    with app.app_context():number=db.session.get(StaffMember,sid).staff_number
    post(recovery,'/staff/recover',page='/staff/recover',staff_number=number,email='staff@example.test');code=re.search(r'code is (\d{6})',sent[-1]['body'])[1]
    assert post(recovery,'/staff/verify',page='/staff/verify',code=code,password='new-staff-password',confirm_password='new-staff-password').status_code==200
    assert client.get('/staff').status_code==302


def test_drafts_and_private_attachments_do_not_leak_to_supervisor(app):
    manager,sent=setup(app);sup=create(app,'Supervisor','supervisor');sid=create(app,'Employee',supervisor=sup)
    worker=login(app,sent,sid);supervisor=login(app,sent,sup)
    day=(now()-timedelta(days=1)).date().isoformat()
    assert post(worker,'/staff/reports',action='draft',report_date=day,completed='Private draft',outcomes='Not ready').status_code==200
    with app.app_context():rid=db.session.scalar(select(StaffReport.id))
    assert supervisor.get(f'/staff/reports/{rid}').status_code==403
    assert manager.get(f'/staff/reports/{rid}').status_code==403
    assert worker.get(f'/staff/reports/{rid}/draft.pdf').data.startswith(b'%PDF-')
    assert supervisor.get(f'/staff/reports/{rid}/draft.pdf').status_code==403
    post(worker,'/staff/documents',kind='government_id',file=(io.BytesIO(b'%PDF-1.7\nprivate ID'),'id.pdf'))
    with app.app_context():doc=db.session.scalar(select(StaffDocument.id))
    assert post(worker,'/staff/reports',action='submit',report_date=day,completed='Tasks',outcomes='Outcomes',supporting_document=doc).status_code==403
    with app.app_context():assert db.session.scalar(select(db.func.count()).select_from(StaffReportVersion))==0


def test_department_and_assigned_grants_do_not_broaden_role(app):
    manager,sent=setup(app);hrid=create(app,'HR','hr',scope='department');a=create(app,'Admissions');b=create(app,'Finance',department='Finance')
    hr=login(app,sent,hrid)
    assert hr.get(f'/staff/personnel/{a}/personal').status_code==200
    assert hr.get(f'/staff/personnel/{b}/personal').status_code==403
    post(manager,f'/admin/team/{hrid}/manage',page='/admin/team',action='permissions',role='hr',scope='assigned',department='Admissions',assigned_member=str(b),admin_password='test-admin-password')
    assert hr.get('/staff').status_code==302
    hr=login(app,sent,hrid)
    assert hr.get(f'/staff/personnel/{b}/personal').status_code==200
    assert hr.get(f'/staff/personnel/{a}/personal').status_code==403
    assert hr.get(f'/staff/personnel/{b}/payroll').status_code==403


def test_report_email_rechecks_supervisor_department_scope(app):
    manager,sent=setup(app);sup=create(app,'Supervisor','supervisor',scope='department',department='Operations');sid=create(app,'Employee',supervisor=sup,department='Admissions')
    worker=login(app,sent,sid);supervisor=login(app,sent,sup)
    assert post(worker,'/staff/reports',action='submit',report_date=(now()-timedelta(days=1)).date().isoformat(),completed='Tasks',outcomes='Outcomes').status_code==200
    with app.app_context():
        rid=db.session.scalar(select(StaffReport.id));copy=db.session.scalar(select(StaffMail).where(StaffMail.kind=='report',StaffMail.member_id==sup));assert copy.status=='cancelled' and not copy.ciphertext
    assert supervisor.get(f'/staff/reports/{rid}').status_code==403
    assert not any(p['to']=='supervisor@example.test' and p['subject'].startswith('Daily staff report') for p in sent)
