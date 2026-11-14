"""Transactional advisor allocation, assigned-record access and durable notices."""
import base64,hashlib,json,os,re,time
from datetime import timedelta
from urllib.parse import quote
from flask import abort,flash,g,redirect,render_template,request,session,url_for
from sqlalchemy import select,text
from providers import ORIGIN,api
from member_contacts import phone_number
from lesson_tasks import task_number
from whatsapp_templates import validate,TemplateError

KINDS=('student_advisor_assigned','advisor_student_assigned','advisor_admin_assigned','advisor_followup_assigned')
CHECKLIST={
 'contact':'Contact the student and confirm their preferred contact details.',
 'program':'Confirm their program, level and preferred start date.',
 'portal':'Help them sign in and find their lessons and payment schedule.',
 'payment':'Guide new students to secure portal checkout; follow up on pending payments.',
 'existing':'For existing students, collect prior tuition information and submit for admin review.',
 'followup':'Record the outcome and agree the next follow-up date.',
}


def define(db,now):
    class StudentAdvisor(db.Model):
        __tablename__='portal_student_advisors'
        student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),primary_key=True)
        task_id=db.Column(db.Integer,db.ForeignKey('staff_tasks.id'),unique=True)
        checklist=db.Column(db.JSON,nullable=False,default=dict)
        assigned_at=db.Column(db.DateTime(timezone=True))
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
        student=db.relationship('Student')
        task=db.relationship('StaffTask')
    class AdvisorRotation(db.Model):
        __tablename__='portal_advisor_rotation'
        id=db.Column(db.Integer,primary_key=True)
        last_member_id=db.Column(db.Integer)
    class AdvisorFollowup(db.Model):
        __tablename__='portal_advisor_followups'
        key=db.Column(db.String(64),primary_key=True)
        student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False,index=True)
        task_id=db.Column(db.Integer,db.ForeignKey('staff_tasks.id'),nullable=False,unique=True)
        reason=db.Column(db.String(40),nullable=False)
        task=db.relationship('StaffTask')
        student=db.relationship('Student')
    return dict(StudentAdvisor=StudentAdvisor,AdvisorRotation=AdvisorRotation,AdvisorFollowup=AdvisorFollowup)


def install(app,db,m,h):
    Advisor,Rotation,Member,Task,Link=(m[k] for k in ('StudentAdvisor','AdvisorRotation','StaffMember','StaffTask','TaskRecord'))
    service=app.extensions['integrations'];notices=app.extensions['notifications'];last_scan=[0.0]
    def configuration():
        raw=app.config.get('STUDENT_ADVISORS',os.environ.get('STUDENT_ADVISORS',''))
        if not raw:return []
        try:
            values=json.loads(raw) if isinstance(raw,str) else raw
            if not isinstance(values,list):return []
            return [v for v in values if isinstance(v,dict) and re.fullmatch(r'LTA-STF-[0-9]{4,}',v.get('staff_number','')) and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',v.get('email','')) and phone_number(v.get('whatsapp',''))]
        except (ValueError,TypeError,AttributeError):return []
    def enabled():return bool(configuration())
    def configured_contact(member):
        for v in configuration():
            if member.staff_number==v['staff_number'] and member.email.lower()==v['email'].lower():return phone_number(v['whatsapp'])
        return ''
    def contact(member):
        row=db.session.get(m['MemberContact'],'staff:'+str(member.id))
        try:return configured_contact(member) or (phone_number(row.whatsapp) if row and row.whatsapp else '')
        except ValueError:return ''
    def pool(target=None):
        target=target or db.session
        configured={v['staff_number']:v for v in configuration()}
        result=[]
        for member in target.scalars(select(Member).where(Member.active.is_(True),Member.verified_at.is_not(None),Member.id>0).order_by(Member.id)):
            expected=configured.get(member.staff_number)
            if expected and member.email.lower()!=expected['email'].lower():continue
            if contact(member):result.append(member)
        return result
    def admin_numbers():
        raw=app.config.get('STUDENT_ADVISOR_ADMIN_WHATSAPP',os.environ.get('STUDENT_ADVISOR_ADMIN_WHATSAPP',''))
        try:return list(dict.fromkeys(phone_number(v) for v in raw.split(',') if phone_number(v)))
        except ValueError:return []
    def allocate(target,assignment,initial=False):
        # The singleton row serializes selection across serverless instances. Cursor
        # advancement, task, record grant and all notices share the signup transaction.
        target.execute(text('INSERT INTO portal_advisor_rotation (id,last_member_id) VALUES (1,NULL) ON CONFLICT (id) DO NOTHING'))
        last=target.scalar(select(Rotation.last_member_id).where(Rotation.id==1).with_for_update())
        members=pool(target)
        if not members:return False
        member=next((v for v in members if last is None or v.id>last),members[0])
        target.execute(text('UPDATE portal_advisor_rotation SET last_member_id=:member WHERE id=1'),{'member':member.id})
        task=assignment.task or Task(member_id=member.id,title='Student advisor · '+assignment.student.name[:130],description='Support onboarding, program selection and payment follow-up. Use the advisor checklist. Existing tuition records require administrator review; new students pay through secure portal checkout.',due_on=m['academy_today']()+timedelta(days=1))
        if assignment.task:
            task.member_id=member.id;task.status='assigned';task.updated_at=m['now']()
            for follow in target.scalars(select(m['AdvisorFollowup']).where(m['AdvisorFollowup'].student_id==assignment.student.id)):follow.task.member_id=member.id
        else:target.add(Link(task=task,student=assignment.student,kind='student'))
        assignment.task=task;assignment.assigned_at=m['now']()
        target.add_all([task,m['StaffAudit'](actor='system:advisor',member_id=member.id,action='student_advisor_assigned',detail='Automatic account advisor allocation')])
        for kind,channel in (('advisor_student_assigned','email'),('advisor_student_assigned','whatsapp'),('advisor_admin_assigned','email')):
            notices['enqueue'](assignment.student,kind,payload={'member_id':member.id},channel=channel,session_=target)
        for recipient in admin_numbers():notices['enqueue'](assignment.student,'advisor_admin_assigned',payload={'member_id':member.id,'admin_number':recipient},channel='whatsapp',session_=target)
        if not initial:notices['enqueue'](assignment.student,'student_advisor_assigned',payload={'member_id':member.id},session_=target)
        return True
    def assign(target,student):
        if not enabled():return
        # One immutable account relationship; enrollment additions never reallocate it.
        existing=next((v for v in target.new if isinstance(v,Advisor) and v.student is student),None)
        existing=existing or (target.get(Advisor,student.id) if student.id else None)
        if existing:return existing
        assignment=Advisor(student=student);target.add(assignment);allocate(target,assignment,initial=True)
        return assignment
    def details(student):
        assignment=db.session.get(Advisor,student.id)
        task=assignment.task if assignment else None;member=db.session.get(Member,task.member_id) if task else None
        if not member or not member.active or not member.verified_at:return None
        whatsapp=contact(member)
        return dict(name=member.name,email=member.email,number=member.staff_number,whatsapp=whatsapp,whatsapp_url='https://wa.me/'+whatsapp[1:] if whatsapp else '',task=task,assignment=assignment)
    def welcome(student):
        info=details(student)
        if not info:return '\nYour student advisor is being allocated. Contact portal support if you need assistance.' if enabled() else ''
        return f"\nYour student advisor is {info['name']}.\nAdvisor email: {info['email']}\nAdvisor WhatsApp: {info['whatsapp']}\nChat with your advisor: {info['whatsapp_url']}\nYou can reach out to your student advisor for any assistance you may need."
    def identity_text(student):
        profile=db.session.get(m['AuthProfile'],student.id)
        programs=list(db.session.scalars(select(m['Enrollment']).where(m['Enrollment'].student_id==student.id)))
        return '\nUsername: '+(profile.username if profile else student.email)+'\nProgram: '+(', '.join(v.course.name for v in programs) or 'Not selected yet—your advisor can help you choose.')
    def contacts_text(student):
        return welcome(student)+'\nImportant contacts to save:\nSupport: +1 (437) 575-6656 · https://wa.me/14375756656\nOperations Manager: +1 (647) 381-8836 · https://wa.me/16473818836'
    def event_task(event,info):
        row=db.session.get(m['AdvisorFollowup'],event.payload.get('followup')) if event.payload.get('followup') else None
        return row.task if row and row.student_id==event.student_id else info['task']
    def valid(event):
        info=details(event.student)
        control=db.session.get(m['StudentControl'],event.student_id)
        return bool(info and event_task(event,info).member_id==event.payload.get('member_id') and info['task'].member_id==event.payload.get('member_id') and not db.session.get(m['StudentArchive'],event.student_id) and (not control or control.active))
    def email(event):
        info=details(event.student);student=event.student;task=event_task(event,info)
        if event.kind=='student_advisor_assigned':return student.email,'Meet your LionTech student advisor',f'Hello {student.name},\nThank you for signing up with LionTech Academy.\nStudent number: {student.student_number}\n'+contacts_text(student)+identity_text(student)+'\nYour portal: '+ORIGIN+'/portal'
        if event.kind=='advisor_admin_assigned':return app.config.get('ADMIN_NOTIFICATION_EMAIL','liontechacademy@gmail.com'),'Student advisor assignment confirmed · '+task_number(task),f'{task_number(task)}\nStudent: {student.name} · {student.student_number}\nAdvisor: {info["name"]} · {info["number"]}\nStudent and advisor email notifications have been queued. Check delivery status: {ORIGIN}/admin/team/student-advisors'
        contacts=app.extensions['member_contacts']['details']('student:'+student.id,student.phone,student.email)
        return info['email'],('Advisor follow-up task assigned · ' if event.kind=='advisor_followup_assigned' else 'New student assigned · ')+task_number(task),f"Hello {info['name']},\nA {'new' if student.kind=='new' else 'existing'} student account is assigned to you.\n{task_number(task)} · {task.title}\nStudent: {student.name}\nStudent number: {student.student_number}\nEmail: {student.email}\nWhatsApp: {contacts['whatsapp'] or 'Not provided'}\nChat with student: {contacts['whatsapp_url'] or 'Use email to confirm their WhatsApp number'}\nOpen assigned student: {ORIGIN}/staff/tasks/{task.id}/record\n\nAdvisor checklist:\n"+'\n'.join('- '+v for v in CHECKLIST.values())+'\nOnly the administrator approves existing tuition records. New students pay through portal checkout.'
    def whatsapp(event):
        info=details(event.student);task=event_task(event,info);values=service['config']('whatsapp')
        if not service['ready']('whatsapp'):event.status='awaiting_whatsapp_template';db.session.commit();return
        if event.kind=='advisor_admin_assigned':
            recipient=event.payload.get('admin_number','')
            if recipient not in admin_numbers():event.status='cancelled';db.session.commit();return
            name='Administrator';label='Student advisor assignment confirmed';summary=f'{task_number(task)} · {event.student.student_number} · Advisor: {info["name"]}'
            link=ORIGIN+'/admin/team/student-advisors'
        else:
            member=db.session.get(Member,task.member_id);preference=db.session.get(m['MemberContact'],'staff:'+str(member.id))
            if not preference or not preference.task_alerts or not preference.consented_at or preference.whatsapp!=contact(member):event.status='awaiting_staff_consent';db.session.commit();return
            recipient=contact(member);name=member.name;label='New student assigned'
            student_contact=app.extensions['member_contacts']['details']('student:'+event.student_id,event.student.phone,event.student.email)
            summary=f'{task_number(task)} · {event.student.name} · WhatsApp: {student_contact["whatsapp_url"] or "not provided"}'
            link=ORIGIN+'/staff/tasks/'+str(task.id)+'/record'
        try:recipient=phone_number(recipient)
        except ValueError:recipient=''
        if not recipient:event.status='invalid_recipient';db.session.commit();return
        sid=values.get('TWILIO_TASK_TEMPLATE_SID','');variables={'1':name,'2':label,'3':summary.replace('\n',' ')[:500],'4':task.due_on.isoformat(),'5':link}
        try:app.config.get('WHATSAPP_TEMPLATE_VALIDATOR',validate)(values,sid,variables)
        except TemplateError as error:event.status=error.status;db.session.commit();return
        if not re.fullmatch(r'HX[0-9a-fA-F]{32}',sid):event.status='awaiting_whatsapp_template';db.session.commit();return
        event.attempts+=1
        credentials=base64.b64encode((values['TWILIO_ACCOUNT_SID']+':'+values['TWILIO_AUTH_TOKEN']).encode()).decode()
        try:
            result=app.config.get('LIFECYCLE_HTTP',api)('https://api.twilio.com/2010-04-01/Accounts/'+quote(values['TWILIO_ACCOUNT_SID'],safe='')+'/Messages.json','POST',{'From':values['TWILIO_WHATSAPP_FROM'],'To':'whatsapp:'+recipient,'ContentSid':sid,'ContentVariables':json.dumps(variables)},{'Authorization':'Basic '+credentials},True)
            provider=str(result.get('sid',''))
            if not re.fullmatch(r'SM[0-9a-fA-F]{32}',provider):raise ValueError('Unconfirmed provider acceptance')
            event.provider_id=provider;event.status='accepted';event.accepted_at=m['now']()
        except Exception:event.status='uncertain';app.logger.warning('Advisor WhatsApp acceptance uncertain; check provider logs before resending.')
        db.session.commit()
    def followup(target,event):
        if not enabled() or event.kind not in ('payment_due','payment_deletion_warning','assignment_due'):return
        if db.engine.dialect.name=='postgresql':target.execute(text('SELECT pg_advisory_xact_lock(73592062)'))
        student=event.student or target.get(m['Student'],event.student_id)
        assignment=assign(target,student)
        if not assignment or not assignment.task:return
        key=hashlib.sha256(json.dumps([student.id,event.kind,event.payload.get('enrollment_id'),event.payload.get('assessment_id'),event.payload.get('due'),event.payload.get('generation')],sort_keys=True).encode()).hexdigest()
        if target.get(m['AdvisorFollowup'],key) or any(isinstance(v,m['AdvisorFollowup']) and v.key==key for v in target.new):return
        reason={'payment_due':'Payment follow-up','payment_deletion_warning':'Account deletion warning follow-up','assignment_due':'Course assignment due follow-up'}[event.kind]
        task=Task(member_id=assignment.task.member_id,title=reason+' · '+student.name[:100],description=reason+'. Contact the student using the assigned record, guide them through the portal and record the outcome. Do not approve payments or enrollment.',due_on=m['academy_today']()+timedelta(days=1),priority='high')
        target.add_all([task,m['AdvisorFollowup'](key=key,student=student,task=task,reason=event.kind),Link(task=task,student=student,kind='student'),m['StaffAudit'](actor='system:advisor',member_id=task.member_id,action='advisor_followup_assigned',detail=event.kind+'; student '+student.id)])
        data={'member_id':task.member_id,'followup':key}
        for channel in ('email','whatsapp'):notices['enqueue'](student,'advisor_followup_assigned',payload=data,channel=channel,session_=target)
        notices['enqueue'](student,'advisor_admin_assigned',payload=data,session_=target)
        for recipient in admin_numbers():notices['enqueue'](student,'advisor_admin_assigned',payload={**data,'admin_number':recipient},channel='whatsapp',session_=target)
    def assignment_reminders():
        if not enabled():return 0
        count=0
        for assessment in db.session.scalars(select(m['Assessment']).where(m['Assessment'].due_at>m['now'](),m['Assessment'].due_at<=m['now']()+timedelta(hours=24))):
            for enrollment in db.session.scalars(select(m['Enrollment']).where(m['Enrollment'].course_id==assessment.course_id,m['Enrollment'].status=='approved',m['Enrollment'].payment_status=='verified')):
                control=db.session.get(m['StudentControl'],enrollment.student_id)
                if (control and not control.active) or db.session.get(m['StudentArchive'],enrollment.student_id):continue
                if db.session.scalar(select(m['Submission'].id).where(m['Submission'].student_id==enrollment.student_id,m['Submission'].assessment_id==assessment.id)):continue
                key='assignment-due:'+str(assessment.id)+':'+enrollment.student_id+':'+m['aware'](assessment.due_at).isoformat()
                if db.session.scalar(select(m['LifecycleEvent'].id).where(m['LifecycleEvent'].event_key==key)):continue
                notices['enqueue'](enrollment.student,'assignment_due',key,dict(assessment_id=assessment.id,enrollment_id=enrollment.id,due=m['aware'](assessment.due_at).isoformat()));count+=1
        db.session.commit();return count
    def backfill(limit=5):
        if not enabled():return 0
        query=select(m['Student']).where(~m['Student'].id.in_(select(Advisor.student_id)),~m['Student'].id.in_(select(m['StudentArchive'].student_id)),~m['Student'].id.in_(select(m['StudentControl'].student_id).where(m['StudentControl'].active.is_(False)))).order_by(m['Student'].created_at,m['Student'].id).limit(limit)
        # Serialize backlog claiming before selecting missing relationships.
        if db.engine.dialect.name=='postgresql':db.session.execute(text('SELECT pg_advisory_xact_lock(73592061)'))
        count=0
        for student in list(db.session.scalars(query)):
            assignment=Advisor(student=student);db.session.add(assignment);allocate(db.session,assignment);count+=1
        db.session.flush()
        for orphan in list(db.session.scalars(select(Advisor).join(Task,Task.id==Advisor.task_id).join(Member,Member.id==Task.member_id).where((Member.active.is_(False)) | (Member.verified_at.is_(None))).limit(limit))):
            if allocate(db.session,orphan):count+=1
        for assignment in list(db.session.scalars(select(Advisor).where(Advisor.task_id.is_(None)).order_by(Advisor.created_at).limit(limit))):
            if allocate(db.session,assignment):count+=1
        db.session.commit();return count
    @app.after_request
    def automatic_backlog(response):
        if not app.testing and enabled() and response.status_code<400 and request.endpoint not in ('static','language_catalog') and time.monotonic()-last_scan[0]>30:
            last_scan[0]=time.monotonic()
            try:backfill()
            except Exception:db.session.rollback();app.logger.warning('Student advisor backlog retained for retry.')
        return response
    @app.route('/admin/team/student-advisors',methods=['GET','POST'])
    @h['admin_required']
    def admin_student_advisors():
        if request.method=='POST':
            service['reauthenticate']();backfill(100);notices['drain'](200);flash('Automatic allocation and pending notification delivery checked.');return redirect(request.path)
        rows=list(db.session.scalars(select(Advisor).order_by(Advisor.created_at.desc()).limit(200)))
        ids=[v.student_id for v in rows]
        delivery=list(db.session.scalars(select(m['LifecycleEvent']).where(m['LifecycleEvent'].student_id.in_(ids),m['LifecycleEvent'].kind.in_(KINDS)).order_by(m['LifecycleEvent'].created_at.desc()).limit(300)))
        return render_template('student_advisors_admin.html',assignments=rows,advisors=pool(),advisor_delivery=delivery,checklist=CHECKLIST)
    @app.get('/staff/my-students')
    def advisor_students():
        if not getattr(g,'staff',None):return redirect(url_for('staff_login'))
        rows=list(db.session.scalars(select(Advisor).join(Task,Task.id==Advisor.task_id).where(Task.member_id==g.staff.id).order_by(Advisor.assigned_at.desc())))
        return render_template('advisor_students.html',assignments=rows)
    @app.post('/staff/tasks/<int:task_id>/advisor-checklist')
    def advisor_checklist(task_id):
        assignment=db.session.scalar(select(Advisor).where(Advisor.task_id==task_id).with_for_update())
        if not assignment or not getattr(g,'staff',None) or assignment.task.member_id!=g.staff.id:abort(403)
        assignment.checklist={k:request.form.get(k)=='yes' for k in CHECKLIST};db.session.commit();flash('Advisor checklist saved.');return redirect(url_for('staff_task_record',task_id=task_id))
    @app.context_processor
    def context():return dict(student_advisor=details,automatic_advisors_enabled=enabled(),automatic_advisor_task=lambda identity:bool(db.session.scalar(select(Advisor).where(Advisor.task_id==identity)) or db.session.scalar(select(m['AdvisorFollowup']).where(m['AdvisorFollowup'].task_id==identity))),advisor_enrollments=lambda student:list(db.session.scalars(select(m['Enrollment']).where(m['Enrollment'].student_id==student.id))),advisor_checklist=CHECKLIST,advisor_for_task=lambda identity:db.session.scalar(select(Advisor).where(Advisor.task_id==identity)))
    app.extensions['student_advisors']=dict(assign=assign,details=details,welcome=welcome,email=email,valid=valid,whatsapp=whatsapp,backfill=backfill,enabled=enabled,followup=followup,contacts_text=contacts_text,assignment_reminders=assignment_reminders,identity_text=identity_text)
