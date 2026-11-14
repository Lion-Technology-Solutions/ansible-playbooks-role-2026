import json
from datetime import timedelta
from sqlalchemy import select,func
from app import db,Student,StudentAdvisor,AdvisorRotation,AdvisorFollowup,StaffMember,StaffTask,TaskRecord,LifecycleEvent,MemberContact,StudentArchive,Enrollment,Assessment,Submission,now
from test_portal import register,csrf,request_enrollment,analytics
from test_staff_portal import setup,create,login,post


def configured(app):
    manager,sent=setup(app)
    first=create(app,'Advisor One');second=create(app,'Advisor Two')
    with app.app_context():
        people=[db.session.get(StaffMember,v) for v in (first,second)]
        app.config['STUDENT_ADVISORS']=[dict(staff_number=p.staff_number,email=p.email,whatsapp='+1555000000'+str(p.id)) for p in people]
    app.config['STUDENT_ADVISOR_ADMIN_WHATSAPP']='+15551111111,+15552222222'
    return manager,sent,first,second


def count(model):return db.session.scalar(select(func.count()).select_from(model))


def test_signup_rotates_once_both_student_types_and_keeps_enrollment_advisor(app):
    _,_,first,second=configured(app)
    clients=[]
    for i,kind in enumerate(('new','existing','new','existing')):
        client=app.test_client();clients.append(client);assert register(client,f'advisor-student-{i}@example.test',kind=kind).status_code==200
    with app.app_context():
        rows=list(db.session.scalars(select(StudentAdvisor).order_by(StudentAdvisor.created_at)))
        assert [r.task.member_id for r in rows]==[first,second,first,second]
        assert count(StaffTask)==4 and count(TaskRecord)==4
        sid=rows[0].student_id;tid=rows[0].task_id
        events=list(db.session.scalars(select(LifecycleEvent).where(LifecycleEvent.student_id==sid)))
        assert len([e for e in events if e.kind=='advisor_admin_assigned' and e.channel=='whatsapp'])==2
    cid,cohort=analytics(app);request_enrollment(clients[0],cid,cohort)
    register(app.test_client(),'advisor-student-0@example.test')
    with app.app_context():
        assert db.session.get(StudentAdvisor,sid).task_id==tid and count(StudentAdvisor)==4
        assert db.session.get(AdvisorRotation,1).last_member_id==second


def test_failed_signup_rolls_back_rotation_tasks_and_notices(app):
    _,_,first,_=configured(app)
    result=app.test_client().post('/register',data={'csrf':csrf(app.test_client(),'/register')})
    with app.app_context():assert count(StudentAdvisor)==0 and count(StaffTask)==0
    # Exercise a rollback after allocation and flush, not just form validation.
    with app.app_context():
        student=Student(name='Rolled back',email='rollback@example.test',phone='+15550003333',country='Canada',kind='new',password_hash='unused')
        db.session.add(student);db.session.flush();assert count(StudentAdvisor)==1;db.session.rollback()
        assert count(StudentAdvisor)==0 and count(StaffTask)==0 and count(AdvisorRotation)==0
    register(app.test_client())
    with app.app_context():assert db.session.scalar(select(StudentAdvisor)).task.member_id==first


def test_pool_skips_inactive_identity_mismatch_and_new_staff_joins_automatically(app):
    _,_,first,second=configured(app)
    third=create(app,'New Teaching Staff',role='instructor')
    with app.app_context():
        db.session.add(MemberContact(owner='staff:'+str(third),whatsapp='+15553333333'))
        db.session.get(StaffMember,first).active=False;db.session.commit()
    register(app.test_client(),'one@example.test');register(app.test_client(),'two@example.test')
    with app.app_context():
        assert [r.task.member_id for r in db.session.scalars(select(StudentAdvisor).order_by(StudentAdvisor.created_at))]==[second,third]
        db.session.get(StaffMember,second).email='changed@example.test';db.session.commit()
    register(app.test_client(),'three@example.test')
    with app.app_context():assert list(db.session.scalars(select(StudentAdvisor).order_by(StudentAdvisor.created_at)))[-1].task.member_id==third


def test_pending_allocation_and_existing_account_backfill_are_idempotent(app):
    manager,sent,first,second=configured(app)
    with app.app_context():
        for member in db.session.scalars(select(StaffMember)):member.active=False
        db.session.commit()
    register(app.test_client(),'pending@example.test',kind='existing')
    with app.app_context():
        assert not db.session.scalar(select(StudentAdvisor)).task_id
        db.session.get(StaffMember,first).active=True;db.session.commit()
        app.extensions['student_advisors']['backfill']();app.extensions['student_advisors']['backfill']()
        assert count(StudentAdvisor)==1 and count(StaffTask)==1
        assert count(LifecycleEvent)>0
        assert len(list(db.session.scalars(select(LifecycleEvent).where(LifecycleEvent.kind=='student_advisor_assigned'))))==1
    app.config['STUDENT_ADVISORS']=[];register(app.test_client(),'legacy@example.test',kind='existing')
    with app.app_context():
        member=db.session.get(StaffMember,first)
        app.config['STUDENT_ADVISORS']=[dict(staff_number=member.staff_number,email=member.email,whatsapp='+15550000001')]
        assert app.extensions['student_advisors']['backfill']()==1
        assert app.extensions['student_advisors']['backfill']()==0
        assert count(StudentAdvisor)==2 and count(StaffTask)==2


def test_student_card_staff_directory_checklist_and_manual_allocation_guard(app):
    manager,sent,first,second=configured(app);student=app.test_client();register(student)
    worker=login(app,sent,first);other=login(app,sent,second)
    page=student.get('/portal');assert b'Your student advisor' in page.data and b'Advisor One' in page.data and b'https://wa.me/15550000001' in page.data
    with app.app_context():assignment=db.session.scalar(select(StudentAdvisor));tid=assignment.task_id;sid=assignment.student_id
    assert b'Student Example' in worker.get('/staff/my-students').data
    assert b'Student Example' not in other.get('/staff/my-students').data
    assert other.get(f'/staff/tasks/{tid}/record').status_code==403
    assert student.get('/staff/my-students').status_code==302
    assert post(worker,f'/staff/tasks/{tid}/advisor-checklist',contact='yes',program='yes').status_code==200
    with app.app_context():assert db.session.get(StudentAdvisor,sid).checklist['contact'] is True
    assert other.post(f'/staff/tasks/{tid}/advisor-checklist',data={'csrf':csrf(other,'/staff'),'contact':'yes'}).status_code==403
    assert manager.post('/staff/workboard',data={'csrf':csrf(manager,'/admin')}).status_code==409
    assert manager.post(f'/admin/team/tasks/{tid}/reassign',data={'csrf':csrf(manager,'/admin'),'member_id':second}).status_code==409
    assert worker.get(f'/staff/tasks/{tid}/enrollment').status_code==409
    assert manager.get('/admin/team/student-advisors').status_code==200


def enable_email(app):
    service=app.extensions['integrations'];config=service['config'];ready=service['ready']
    service['config']=lambda provider,*a,**kw: {'SENDGRID_API_KEY':'test','SENDGRID_FROM_EMAIL':'portal@example.test'} if provider=='email' else {'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'unit','TWILIO_WHATSAPP_FROM':'whatsapp:+15554444444','TWILIO_TASK_TEMPLATE_SID':'HX'+'b'*32} if provider=='whatsapp' else config(provider,*a,**kw)
    service['ready']=lambda provider:True if provider in ('email','whatsapp') else ready(provider)


def test_welcome_email_student_identity_advisor_program_and_wrapped_chat(app):
    _,_,first,_=configured(app);enable_email(app);sent=[];app.config['LIFECYCLE_HTTP']=lambda *args:sent.append(args) or {'sid':'SM'+'a'*32}
    client=app.test_client();client.get('/');cid,cohort=analytics(app)
    with client.session_transaction() as state:state['application']={'course_id':cid,'cohort_id':cohort}
    register(client)
    with app.app_context():
        row=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='account_created'))
        app.extensions['notifications']['process'](row.id)
        welcome=sent[-1][2];assert welcome['personalizations'][0]['to'][0]['email']=='student@example.com'
        plain,html=[v['value'] for v in welcome['content']]
        assert row.student.student_number in plain and 'Username:' in plain and 'Program: Data Analytics' in plain
        assert 'Advisor One' in html and 'Chat with your advisor</a>' in html and 'https://wa.me/15550000001' in html
        assert '14375756656' in html and '16473818836' in html
        advisor_event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='advisor_student_assigned',LifecycleEvent.channel=='email'))
        app.extensions['notifications']['process'](advisor_event.id)
        message=sent[-1][2];assert message['personalizations'][0]['to'][0]['email']=='advisorone@example.test'
        assert 'https://wa.me/237600000000' in message['content'][0]['value']
        assert '/staff/tasks/' in message['content'][0]['value']


def test_payment_deletion_and_assignment_notices_create_deduplicated_followup(app):
    _,_,first,_=configured(app);register(app.test_client())
    with app.app_context():
        student=db.session.scalar(select(Student));service=app.extensions['notifications']
        for kind,data in [('payment_due',{'enrollment_id':1,'due':'2026-12-01'}),('payment_deletion_warning',{'generation':'unit-case'}),('assignment_due',{'assessment_id':1,'due':'2026-12-02'})]:
            service['enqueue'](student,kind,payload=data);db.session.commit()
            service['enqueue'](student,kind,payload=data);db.session.commit()
        assert count(AdvisorFollowup)==3 and count(StaffTask)==4
        assert all(row.task.member_id==first for row in db.session.scalars(select(AdvisorFollowup)))


def test_admin_whatsapp_independent_recipients_and_uncertain_never_resends(app):
    _,_,_,_=configured(app);register(app.test_client());enable_email(app)
    sent=[];app.config['LIFECYCLE_HTTP']=lambda *a:sent.append(a) or {'sid':'SM'+'a'*32}
    with app.app_context():
        rows=list(db.session.scalars(select(LifecycleEvent).where(LifecycleEvent.kind=='advisor_admin_assigned',LifecycleEvent.channel=='whatsapp')))
        for row in rows:app.extensions['notifications']['process'](row.id)
        assert {a[2]['To'] for a in sent}=={'whatsapp:+15551111111','whatsapp:+15552222222'}
        assert all(row.status=='accepted' for row in rows)
        assert all('Student advisor assignment confirmed' in json.loads(a[2]['ContentVariables'])['2'] for a in sent)
        row=rows[0];row.status='pending';db.session.commit()
        def uncertain(*a):raise TimeoutError()
        app.config['LIFECYCLE_HTTP']=uncertain
        app.extensions['notifications']['process'](row.id);assert row.status=='uncertain'
        app.config['LIFECYCLE_HTTP']=lambda *a:sent.append(a)
        app.extensions['notifications']['process'](row.id);assert len(sent)==2


def test_assignment_due_only_paid_unsubmitted_and_cancel_after_submission(app):
    _,_,_,_=configured(app);register(app.test_client());cid,cohort=analytics(app)
    with app.app_context():
        student=db.session.scalar(select(Student));e=Enrollment(student_id=student.id,course_id=cid,cohort_id=cohort,status='approved',payment_status='verified');db.session.add(e)
        assessment=Assessment(course_id=cid,title='Practice due',kind='assignment',instructions='Practice',due_at=now()+timedelta(hours=20));db.session.add(assessment);db.session.commit()
        assert app.extensions['student_advisors']['assignment_reminders']()==1
        assert app.extensions['student_advisors']['assignment_reminders']()==0
        row=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='assignment_due'))
        db.session.add(Submission(student_id=student.id,assessment_id=assessment.id,answer='Completed'));db.session.commit()
        app.extensions['notifications']['process'](row.id);assert row.status=='cancelled'


def test_inactive_advisor_reassigned_automatically_and_old_notice_cancelled(app):
    _,_,first,second=configured(app);register(app.test_client())
    with app.app_context():
        original=db.session.scalar(select(StudentAdvisor));task_id=original.task_id
        old=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='advisor_student_assigned',LifecycleEvent.channel=='email'))
        db.session.get(StaffMember,first).active=False;db.session.commit()
        app.extensions['student_advisors']['backfill']()
        assert original.task_id==task_id and original.task.member_id==second and count(StaffTask)==1
        app.extensions['notifications']['process'](old.id);assert old.status=='cancelled'
        assert len(list(db.session.scalars(select(LifecycleEvent).where(LifecycleEvent.kind=='student_advisor_assigned'))))==1


def test_parallel_account_transactions_share_one_persistent_rotation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app import create_app,AuthPolicy
    application=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':'sqlite:///'+str(tmp_path/'advisors.db'),'PAYMENT_RETENTION_ENABLED':False,'BONANZA_ENABLED':False,'DATA_ANALYTICS_LINK_PLAN':False,'SESSION_COOKIE_SECURE':False})
    with application.app_context():
        db.create_all();db.session.add(AuthPolicy(id=1,require_email=False,require_mfa=False));db.session.commit()
    try:
        _,_,first,second=configured(application)
        from localization import CURRENCIES
        application.config['FX_PROVIDER']=lambda:({code:'1' for code in CURRENCIES},now())
        application.test_client().get('/')
        def signup(i):
            with application.app_context():
                db.session.add(Student(name='Parallel '+str(i),email=f'parallel-{i}@example.test',phone='+15550004444',country='Canada',kind='new',password_hash='test-only'))
                db.session.commit()
                return True
        with ThreadPoolExecutor(max_workers=4) as executor:assert all(executor.map(signup,range(4)))
        with application.app_context():
            assert count(StudentAdvisor)==4 and count(StaffTask)==4
            ids=list(db.session.scalars(select(StaffTask.member_id).order_by(StaffTask.id)))
            assert ids==[first,second,first,second]
    finally:
        with application.app_context():db.session.remove();db.engine.dispose()
