"""Durable administrator review alerts, independent email and WhatsApp delivery."""
import base64,json,re,secrets
from urllib.parse import quote
from flask import g,session,request
from sqlalchemy import select
from providers import ORIGIN,api
from lesson_tasks import task_number
from whatsapp_templates import validate,TemplateError


def define(db,now):
    class LessonReviewNotice(db.Model):
        __tablename__='staff_lesson_review_notices'
        id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
        submission_id=db.Column(db.String(32),db.ForeignKey('staff_lesson_submissions.id'),nullable=False,index=True)
        channel=db.Column(db.String(12),nullable=False)
        status=db.Column(db.String(40),nullable=False,default='pending')
        attempts=db.Column(db.Integer,nullable=False,default=0)
        provider_id=db.Column(db.String(100),nullable=False,default='')
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
        __table_args__=(db.UniqueConstraint('submission_id','channel'),)
    return dict(LessonReviewNotice=LessonReviewNotice)


def install(app,db,m,h):
    Notice=m['LessonReviewNotice'];service=app.extensions['integrations']
    def enqueue(row,task,lesson):
        for channel in ('email','whatsapp'):db.session.add(Notice(submission_id=row.id,channel=channel))
        g.lesson_notices_dirty=True
    def process(identity):
        notice=db.session.scalar(select(Notice).where(Notice.id==identity).with_for_update().execution_options(populate_existing=True))
        if not notice or notice.status in ('accepted','cancelled','uncertain') or notice.attempts>=5:return
        row=db.session.get(m['LessonSubmission'],notice.submission_id)
        if not row or row.status!='submitted' or not app.extensions['lesson_tasks']['active'](row):notice.status='cancelled';db.session.commit();return
        task=db.session.get(m['StaffTask'],row.task_id);assignment=db.session.get(m['LessonTask'],task.id);lesson=db.session.get(m['Lesson'],assignment.lesson_id);member=db.session.get(m['StaffMember'],row.preparer_id)
        link=ORIGIN+'/admin/lesson-submissions/'+row.id;transport=app.config.get('LESSON_NOTICE_HTTP',api)
        if not service['ready']('email' if notice.channel=='email' else 'whatsapp'):notice.status='awaiting_provider';db.session.commit();return
        values=service['config']('email' if notice.channel=='email' else 'whatsapp')
        if notice.channel=='email':
            body=f'{task_number(task)}\nLesson: {lesson.title}\nProgram: {lesson.course.name}\nSubmitted by: {member.name}\nRevision: {row.revision}\nReview and approve: {link}\nAdministrator sign-in is required. Draft documents are private until approval.'
            payload={'personalizations':[{'to':[{'email':app.config.get('ADMIN_NOTIFICATION_EMAIL','liontechacademy@gmail.com')}]}],'from':{'email':values['SENDGRID_FROM_EMAIL']},'subject':'Lesson ready for review · '+task_number(task),'content':[{'type':'text/plain','value':body}]}
            endpoint='https://api.sendgrid.com/v3/mail/send';headers={'Authorization':'Bearer '+values['SENDGRID_API_KEY']};form=False
        else:
            recipient=values.get('TWILIO_REVIEW_TO','');sid=values.get('TWILIO_REVIEW_TEMPLATE_SID','')
            if not re.fullmatch(r'\+[1-9][0-9]{6,14}',recipient):notice.status='awaiting_admin_number';db.session.commit();return
            variables={'1':task_number(task),'2':lesson.title.replace('\n',' ')[:150],'3':lesson.course.name.replace('\n',' ')[:150],'4':member.name.replace('\n',' ')[:100],'5':link}
            try:app.config.get('WHATSAPP_TEMPLATE_VALIDATOR',validate)(values,sid,variables)
            except TemplateError as error:notice.status=error.status;db.session.commit();return
            credentials=base64.b64encode((values['TWILIO_ACCOUNT_SID']+':'+values['TWILIO_AUTH_TOKEN']).encode()).decode()
            endpoint='https://api.twilio.com/2010-04-01/Accounts/'+quote(values['TWILIO_ACCOUNT_SID'],safe='')+'/Messages.json';headers={'Authorization':'Basic '+credentials};form=True
            payload={'From':values['TWILIO_WHATSAPP_FROM'],'To':'whatsapp:'+recipient,'ContentSid':sid,'ContentVariables':json.dumps(variables)}
        notice.attempts+=1
        try:
            result=transport(endpoint,'POST',payload,headers,form)
            if notice.channel=='whatsapp':
                identity=str((result or {}).get('sid',''))
                if not re.fullmatch(r'SM[0-9a-fA-F]{32}',identity):raise ValueError('Provider acceptance not confirmed.')
                notice.provider_id=identity
            notice.status='accepted'
        except Exception:
            # Do not duplicate an alert when the remote server may have accepted it.
            notice.status='uncertain';app.logger.warning('Lesson review notification acceptance uncertain; inspect provider delivery activity.')
        db.session.commit()
    def drain(limit=100):
        if app.testing and not app.config.get('LESSON_NOTICE_DELIVERY'):return 0
        ids=list(db.session.scalars(select(Notice.id).where(Notice.status.notin_(('accepted','cancelled','uncertain')),Notice.attempts<5).order_by(Notice.created_at).limit(limit)))
        for identity in ids:process(identity)
        return len(ids)
    @app.post('/admin/lesson-review-notices/retry')
    @h['admin_required']
    def lesson_review_notice_retry():
        from flask import redirect,url_for,flash
        service['reauthenticate']();drain();flash('Outstanding lesson-review notification setup and delivery checked. Uncertain deliveries require provider verification before resending.');return redirect(url_for('admin_lesson_submissions'))
    @app.after_request
    def delivery(response):
        if getattr(g,'lesson_notices_dirty',False) and response.status_code<400:
            g.lesson_notices_dirty=False
            try:drain()
            except Exception:db.session.rollback();app.logger.warning('Lesson review notice retained for retry.')
        return response
    app.extensions['lesson_review_notices']=dict(enqueue=enqueue,process=process,drain=drain)
