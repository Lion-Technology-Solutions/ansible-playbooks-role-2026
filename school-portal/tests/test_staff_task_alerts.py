import json
from datetime import timedelta
from sqlalchemy import select
from app import db,StaffTask,StaffTaskNotice,TaskAlertState,MemberContact,StaffMail,now
from test_staff_portal import setup,create,login,post


def setup_alerts(app,consent=True):
    manager,sent=setup(app);sid=create(app,'TaskEmployee');worker=login(app,sent,sid)
    values={'TWILIO_ACCOUNT_SID':'AC'+'1'*32,'TWILIO_AUTH_TOKEN':'fake-auth-token','TWILIO_WHATSAPP_FROM':'whatsapp:+15555555555','TWILIO_TASK_TEMPLATE_SID':'HX'+'2'*32}
    service=app.extensions['integrations'];original_config=service['config'];original_ready=service['ready']
    service['config']=lambda provider,*a,**kw:values if provider=='whatsapp' else original_config(provider,*a,**kw)
    service['ready']=lambda provider:True if provider=='whatsapp' else original_ready(provider)
    requests=[];published=[]
    app.config['STAFF_TASK_HTTP']=lambda *a,**kw:(requests.append((a,kw)) or {'sid':'SM'+'3'*32})
    app.config['TASK_ALERT_PUBLISH']=lambda payload,delay:published.append((payload,delay))
    if consent:
        assert post(worker,'/account/contact',page='/account/contact',phone='+15555555556',whatsapp='+15555555557',task_alerts='yes').status_code==200
    due=(now()+timedelta(days=2)).date().isoformat()
    assert post(manager,'/staff/tasks',page='/admin/team',member_id=sid,title='Follow up on account',description='Record outcome',priority='high',due_on=due).status_code==200
    with app.app_context():
        task_id=db.session.scalar(select(StaffTask.id));notice=db.session.scalar(select(StaffTaskNotice).where(StaffTaskNotice.kind=='assigned'));notice_id=notice.id
    return manager,worker,sid,task_id,notice_id,sent,requests,published,values


def fire(app,identity):
    with app.app_context():app.extensions['task_alerts']['process'](identity)


def test_assignment_whatsapp_and_email_dedup_submission_and_due_scheduling(app):
    manager,worker,sid,tid,nid,sent,requests,published,values=setup_alerts(app)
    assert any(p['id']==nid for p,delay in published)
    with app.app_context():
        notices=list(db.session.scalars(select(StaffTaskNotice)));assert {n.kind for n in notices}=={'assigned','due_24h','due_1h'}
    fire(app,nid);fire(app,nid)
    assert len(requests)==1
    payload=requests[0][0][2];assert payload['To']=='whatsapp:+15555555557' and payload['ContentSid']==values['TWILIO_TASK_TEMPLATE_SID']
    variables=json.loads(payload['ContentVariables']);assert variables['2']=='New task assigned' and '17:00' in variables['4']
    assert any(m['subject']=='New LionTech staff task' for m in sent)
    assert post(worker,f'/staff/tasks/{tid}',status='in_progress',action='submit_task',result='Student contacted').status_code==200
    with app.app_context():submitted=db.session.scalar(select(StaffTaskNotice.id).where(StaffTaskNotice.kind=='submitted'))
    fire(app,submitted);fire(app,submitted)
    assert len(requests)==2 and json.loads(requests[-1][0][2]['ContentVariables'])['2']=='Task submission received'
    assert any(m['subject']=='Task submission received' for m in sent)
    with app.app_context():
        notice=db.session.scalar(select(StaffTaskNotice).where(StaffTaskNotice.kind=='due_24h'));due_id=notice.id;instant=notice.due_at
    app.config['TASK_ALERT_CLOCK']=lambda:__import__('app').aware(instant)
    fire(app,due_id)
    with app.app_context():assert db.session.get(StaffTaskNotice,due_id).status=='cancelled'
    assert len(requests)==2


def test_due_alerts_send_once_and_cancel_after_reassignment_or_consent_revocation(app):
    manager,worker,sid,tid,nid,sent,requests,published,values=setup_alerts(app)
    with app.app_context():
        row=db.session.scalar(select(StaffTaskNotice).where(StaffTaskNotice.kind=='due_24h'));due_id=row.id;instant=__import__('app').aware(row.due_at)
    app.config['TASK_ALERT_CLOCK']=lambda:instant
    fire(app,due_id);fire(app,due_id)
    assert len(requests)==1 and json.loads(requests[0][0][2]['ContentVariables'])['2']=='Task due in 24 hours'
    assert sum(m['subject']=='Task due in 24 hours' for m in sent)==1
    other=create(app,'NewOwner')
    assert post(manager,f'/admin/team/tasks/{tid}/reassign',page='/admin/team',member_id=other).status_code==200
    fire(app,nid)
    with app.app_context():assert db.session.get(StaffTaskNotice,nid).status=='cancelled'
    assert len(requests)==1


def test_missing_consent_template_and_uncertain_delivery_visible_without_false_success(app):
    manager,worker,sid,tid,nid,sent,requests,published,values=setup_alerts(app,consent=False)
    fire(app,nid)
    with app.app_context():assert db.session.get(StaffTaskNotice,nid).status=='awaiting_consent'
    assert not requests
    assert post(worker,'/account/contact',page='/account/contact',whatsapp='+15555555557',task_alerts='yes').status_code==200
    values['TWILIO_TASK_TEMPLATE_SID']='';fire(app,nid)
    with app.app_context():assert db.session.get(StaffTaskNotice,nid).status=='awaiting_template'
    assert b'Awaiting Template' in worker.get('/staff/task-notifications').data
    values['TWILIO_TASK_TEMPLATE_SID']='HX'+'2'*32
    def timeout(*a,**kw):requests.append((a,kw));raise TimeoutError()
    app.config['STAFF_TASK_HTTP']=timeout
    fire(app,nid);fire(app,nid)
    with app.app_context():assert db.session.get(StaffTaskNotice,nid).status=='uncertain'
    assert len(requests)==1
    other=create(app,'Other');outsider=login(app,sent,other)
    assert b'Uncertain' not in outsider.get('/staff/task-notifications').data
    assert app.test_client().get('/staff/task-notifications').status_code==403
