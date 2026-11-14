"""Durable staff task alerts; assignment, upcoming deadlines and submission receipt."""
import base64,json,re,secrets
from datetime import datetime,time,timedelta,timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo
from flask import g,session,request,abort,render_template
from sqlalchemy import select,text
from providers import ORIGIN,api
from whatsapp_templates import validate,TemplateError
from lesson_tasks import task_number


def define(db,now):
    class TaskAlertState(db.Model):
        __tablename__='staff_task_alert_state'
        task_id=db.Column(db.Integer,db.ForeignKey('staff_tasks.id'),primary_key=True)
        revision=db.Column(db.Integer,nullable=False,default=1)
    class StaffTaskNotice(db.Model):
        __tablename__='staff_task_notices'
        id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
        event_key=db.Column(db.String(180),nullable=False,unique=True)
        task_id=db.Column(db.Integer,db.ForeignKey('staff_tasks.id'),nullable=False,index=True)
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),nullable=False)
        revision=db.Column(db.Integer,nullable=False)
        kind=db.Column(db.String(25),nullable=False)
        due_at=db.Column(db.DateTime(timezone=True),nullable=False)
        status=db.Column(db.String(40),nullable=False,default='pending')
        attempts=db.Column(db.Integer,nullable=False,default=0)
        publish_attempts=db.Column(db.Integer,nullable=False,default=0)
        provider_id=db.Column(db.String(100),nullable=False,default='')
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    return dict(TaskAlertState=TaskAlertState,StaffTaskNotice=StaffTaskNotice)


def install(app,db,m):
    State,Notice,Task,Member=(m[k] for k in ('TaskAlertState','StaffTaskNotice','StaffTask','StaffMember'))
    service=app.extensions['integrations'];aware=m['aware']
    def clock():return app.config.get('TASK_ALERT_CLOCK',m['now'])()
    def deadline(task,member):return datetime.combine(task.due_on,time(17),ZoneInfo(member.timezone)).astimezone(timezone.utc)
    def enqueue(task,state,kind,due):
        key=f'task:{task.id}:{state.revision}:{kind}'
        existing=db.session.scalar(select(Notice).where(Notice.event_key==key))
        if existing and kind.startswith('due_') and aware(existing.due_at)!=aware(due) and existing.status not in ('accepted','uncertain'):
            existing.due_at=due;existing.status='pending'
        if not existing:
            db.session.add(Notice(event_key=key,task_id=task.id,member_id=task.member_id,revision=state.revision,kind=kind,due_at=due))
    def assigned(task):
        state=db.session.scalar(select(State).where(State.task_id==task.id).with_for_update())
        if state:state.revision+=1
        else:state=State(task_id=task.id,revision=1);db.session.add(state)
        enqueue(task,state,'assigned',clock());g.task_alert_dirty=True
    def submitted(task):
        state=db.session.scalar(select(State).where(State.task_id==task.id).with_for_update())
        if not state:state=State(task_id=task.id,revision=1);db.session.add(state)
        enqueue(task,state,'submitted',clock());g.task_alert_dirty=True
    def arm():
        instant=clock()
        if db.engine.dialect.name=='postgresql':db.session.execute(text('SELECT pg_advisory_xact_lock(73592055)'))
        # Startup of an upgraded portal also adopts existing open tasks.
        for task in db.session.scalars(select(Task).where(Task.status!='completed')):
            member=db.session.get(Member,task.member_id)
            if not member or not member.active or not member.verified_at:continue
            state=db.session.scalar(select(State).where(State.task_id==task.id).with_for_update())
            if not state:state=State(task_id=task.id,revision=1);db.session.add(state)
            due=deadline(task,member)
            for kind,offset in (('due_24h',timedelta(hours=24)),('due_1h',timedelta(hours=1))):
                when=due-offset
                if instant<=when<instant+timedelta(days=6):enqueue(task,state,kind,when)
        db.session.commit()
        if app.testing and not app.config.get('TASK_ALERT_PUBLISH'):return 0
        from integration_jobs import publish_task_notice
        publish=app.config.get('TASK_ALERT_PUBLISH',publish_task_notice);count=0
        for identity in list(db.session.scalars(select(Notice.id).where(Notice.status.in_(('pending','failed','awaiting_consent','awaiting_template','awaiting_provider','template_approval_pending','template_fields_mismatch','unsupported_whatsapp_template')),Notice.attempts<5,Notice.due_at<instant+timedelta(days=6)).limit(200))):
            row=db.session.scalar(select(Notice).where(Notice.id==identity).with_for_update().execution_options(populate_existing=True))
            if row.status not in ('pending','failed','awaiting_consent','awaiting_template','awaiting_provider','template_approval_pending','template_fields_mismatch','unsupported_whatsapp_template'):db.session.commit();continue
            row.publish_attempts+=1
            try:publish({'kind':'staff_task_notice','id':row.id,'run':str(row.publish_attempts)},max(timedelta(0),aware(row.due_at)-instant));row.status='queued';count+=1
            except Exception:row.status='failed';app.logger.warning('Staff task alert retained for scheduling retry.')
            db.session.commit()
        return count
    def process(identity):
        row=db.session.scalar(select(Notice).where(Notice.id==identity).with_for_update().execution_options(populate_existing=True))
        if not row or row.status in ('accepted','cancelled','uncertain'):return
        task=db.session.scalar(select(Task).where(Task.id==row.task_id).with_for_update().execution_options(populate_existing=True));member=db.session.get(Member,row.member_id,populate_existing=True);state=db.session.scalar(select(State).where(State.task_id==row.task_id).with_for_update().execution_options(populate_existing=True));instant=clock()
        if not task or not member or not member.active or not member.verified_at or not state or state.revision!=row.revision or task.member_id!=row.member_id or (row.kind=='submitted' and task.status!='completed') or (row.kind!='submitted' and task.status=='completed') or (row.kind.startswith('due_') and deadline(task,member)<=instant):
            row.status='cancelled';db.session.commit();return
        if aware(row.due_at)>instant:raise RuntimeError('This task reminder is not due yet.')
        event={'assigned':'New task assigned','submitted':'Task submission received','due_24h':'Task due in 24 hours','due_1h':'Task due in one hour'}[row.kind]
        local=deadline(task,member).astimezone(ZoneInfo(member.timezone)).strftime('%b %d, %Y %H:%M %Z')
        if row.kind!='assigned':
            mail=app.extensions['staff_portal']['mail'](member,'task',row.event_key+':email',member.email,event,f'{task_number(task)} · {task.title}\nDue: {local}\nView in portal: {ORIGIN}/staff/workboard',task_notice=row.id)
            db.session.commit();app.extensions['staff_portal']['dispatch']([mail.id])
            row=db.session.scalar(select(Notice).where(Notice.id==identity).with_for_update().execution_options(populate_existing=True))
            if row.status in ('accepted','cancelled','uncertain'):return
            task=db.session.scalar(select(Task).where(Task.id==row.task_id).with_for_update().execution_options(populate_existing=True));state=db.session.get(State,row.task_id,populate_existing=True);member=db.session.get(Member,row.member_id,populate_existing=True)
            if not task or task.member_id!=row.member_id or not state or state.revision!=row.revision or not member.active or (row.kind.startswith('due_') and task.status=='completed'):
                row.status='cancelled';db.session.commit();return
        contact=db.session.get(m['MemberContact'],'staff:'+str(member.id))
        if not contact or not contact.task_alerts or not contact.consented_at or not re.fullmatch(r'\+[1-9][0-9]{6,14}',contact.whatsapp):row.status='awaiting_consent';db.session.commit();return
        values=service['config']('whatsapp');sid=values.get('TWILIO_TASK_TEMPLATE_SID','')
        if not service['ready']('whatsapp'):row.status='awaiting_provider';db.session.commit();return
        if not re.fullmatch(r'HX[0-9a-fA-F]{32}',sid):row.status='awaiting_template';db.session.commit();return
        variables={'1':member.name,'2':event,'3':(task_number(task)+' · '+task.title).replace('\r',' ').replace('\n',' '),'4':local,'5':ORIGIN+'/staff/workboard'}
        try:app.config.get('WHATSAPP_TEMPLATE_VALIDATOR',validate)(values,sid,variables)
        except TemplateError as error:row.status=error.status;db.session.commit();return
        if row.attempts>=5:row.status='failed';db.session.commit();return
        row.attempts+=1
        credentials=base64.b64encode((values['TWILIO_ACCOUNT_SID']+':'+values['TWILIO_AUTH_TOKEN']).encode()).decode()
        try:
            result=app.config.get('STAFF_TASK_HTTP',api)('https://api.twilio.com/2010-04-01/Accounts/'+quote(values['TWILIO_ACCOUNT_SID'],safe='')+'/Messages.json','POST',{'From':values['TWILIO_WHATSAPP_FROM'],'To':'whatsapp:'+contact.whatsapp,'ContentSid':sid,'ContentVariables':json.dumps(variables)},{'Authorization':'Basic '+credentials},True)
            identity=str(result.get('sid',''))
            if not re.fullmatch(r'SM[0-9a-fA-F]{32}',identity):raise ValueError('Provider acceptance was not confirmed.')
            row.provider_id=identity;row.status='accepted'
        except Exception:
            # An ambiguous network result may have been accepted. Require review
            # instead of automatically duplicating a WhatsApp message.
            row.status='uncertain';app.logger.warning('Staff task WhatsApp acceptance is uncertain; review provider delivery logs.')
        db.session.commit()
    @app.get('/staff/task-notifications')
    def staff_task_notifications():
        if not session.get('admin') and not g.staff:abort(403)
        query=select(Notice).order_by(Notice.created_at.desc()).limit(100) if session.get('admin') else select(Notice).where(Notice.member_id==g.staff.id).order_by(Notice.created_at.desc()).limit(100)
        return render_template('staff_task_notifications.html',notices=list(db.session.scalars(query)))
    @app.after_request
    def schedule(response):
        if request.endpoint=='static':return response
        if response.status_code<400 and (getattr(g,'task_alert_dirty',False) or (session.get('admin') and request.endpoint=='staff_workboard')):
            g.task_alert_dirty=False
            try:arm()
            except Exception:db.session.rollback();app.logger.warning('Staff task reminder scheduling will retry during daily maintenance.')
        return response
    app.extensions['task_alerts']=dict(assigned=assigned,submitted=submitted,arm=arm,process=process,deadline=deadline)
