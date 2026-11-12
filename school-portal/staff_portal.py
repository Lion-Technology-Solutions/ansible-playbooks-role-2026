"""Invitation-only staff identities, scoped workforce access and versioned daily reports."""
import base64,hashlib,hmac,io,re,secrets,zipfile
from datetime import date,timedelta
from functools import wraps
from zoneinfo import ZoneInfo
from flask import abort,flash,g,redirect,render_template,request,Response,session,url_for
from sqlalchemy import select,or_
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash,check_password_hash
from werkzeug.utils import secure_filename
from providers import ORIGIN,api
from staff_pdf import render as report_pdf,FIELDS
from legal_content import POLICY_VERSION

ROLES={'staff':'Staff','supervisor':'Supervisor','instructor':'Instructor','hr':'HR','payroll':'Payroll'}
SCOPES={'assigned':'Assigned records','department':'Department','organization':'Organization'}
DOCS={'resume':'Resume / CV','government_id':'Government ID','bank_confirmation':'Bank confirmation','employment':'Employment document','report_support':'Report supporting attachment'}


def install(app,db,m,h):
    Member,Invite,Challenge,Grant,Private,Document,Task,Report,Version,Review,Audit,Mail=(m['Staff'+k] for k in ('Member','Invite','Challenge','Grant','PrivateData','Document','Task','Report','ReportVersion','Review','Audit','Mail'))
    now,aware=m['now'],m['aware'];service=app.extensions['integrations'];limit=app.extensions['auth_security']['limit']
    def mac(value):return hmac.new(app.secret_key.encode(),value.encode(),'sha256').hexdigest()
    def actor():return 'admin:1' if session.get('admin') else 'staff:'+str(g.staff.id) if getattr(g,'staff',None) else 'anonymous'
    def audit(action,member=None,detail=''):db.session.add(Audit(actor=actor(),member_id=member.id if member else None,action=action,detail=detail[:1000]))
    def member_required(fn):
        @wraps(fn)
        def wrapped(*a,**kw):
            if not getattr(g,'staff',None):return redirect(url_for('staff_login'))
            return fn(*a,**kw)
        return wrapped
    def scoped_as(viewer,target):
        if not viewer:return False
        return target.id==viewer.id or (viewer.scope=='organization') or (viewer.scope=='department' and target.department==viewer.department) or (viewer.scope=='assigned' and (target.supervisor_id==viewer.id or db.session.get(Grant,(viewer.id,target.id)) is not None))
    def scoped(target):return scoped_as(getattr(g,'staff',None),target)
    def can(target,kind='reports'):
        if kind in ('reports','tasks') and session.get('admin'):return True
        viewer=getattr(g,'staff',None)
        if not viewer:return False
        if target.id==viewer.id:return True
        roles={'reports':('supervisor',),'tasks':('supervisor',),'personal':('hr',),'government_id':('hr',),'resume':('hr',),'employment':('hr',),'payroll':('payroll',),'bank_confirmation':('payroll',),'report_support':('supervisor',)}
        return viewer.role in roles.get(kind,()) and scoped(target)
    def supervisor_name(member):
        supervisor=db.session.get(Member,member.supervisor_id) if member.supervisor_id else None
        return supervisor.name if supervisor else 'Portal administrator'
    def stepup():
        if session.get('staff_stepup_until',0)<now().timestamp():abort(Response(render_template('staff_locked.html'),status=403))
    def mail(member,kind,key,to,subject,body,**extra):
        existing=db.session.scalar(select(Mail).where(Mail.event_key==key))
        if existing:return existing
        row=Mail(id=secrets.token_hex(16),member_id=member.id if member else None,kind=kind,event_key=key,ciphertext='')
        row.ciphertext=service['seal']('staff-mail',row.id,dict(to=to,subject=subject,body=body,**extra));db.session.add(row);db.session.flush();return row
    def process_mail(identity):
        row=db.session.scalar(select(Mail).where(Mail.id==identity).with_for_update().execution_options(populate_existing=True))
        if not row or row.status in ('sent','cancelled') or row.attempts>=5:return
        data=service['unseal']('staff-mail',row.id,row.ciphertext);recipient=db.session.get(Member,row.member_id) if row.member_id else None
        valid=True
        if data.get('invite'):
            inv=db.session.get(Invite,data['invite']);valid=bool(inv and not inv.revoked and not inv.used and aware(inv.expires_at)>now())
        elif data.get('challenge'):
            challenge=db.session.get(Challenge,data['challenge']);valid=bool(challenge and not challenge.used and challenge.attempts<5 and aware(challenge.expires_at)>now() and recipient and challenge.revision==recipient.revision)
        elif recipient:valid=recipient.active and bool(recipient.verified_at)
        if not valid:row.status='cancelled';row.ciphertext='';db.session.commit();return
        row.attempts+=1
        try:
            payload=dict(to=data['to'],subject=data['subject'],body=data['body'],attachments=[])
            if data.get('version'):
                version=db.session.get(Version,data['version']);report=db.session.get(Report,version.report_id);owner=db.session.get(Member,report.member_id)
                if recipient and recipient.id!=owner.id and (recipient.role!='supervisor' or owner.supervisor_id!=recipient.id or not scoped_as(recipient,owner)):
                    row.status='cancelled';row.ciphertext='';db.session.commit();return
                if not recipient and data['to']!=app.extensions['admin_security']['state']().email:
                    row.status='cancelled';row.ciphertext='';db.session.commit();return
                payload['attachments']=[dict(filename=version.payload['reference']+'.pdf',data=report_pdf(version.payload))]
            if app.config.get('STAFF_MAIL_SENDER'):app.config['STAFF_MAIL_SENDER'](payload)
            else:
                if not service['ready']('email'):raise ValueError('Email integration is not ready.')
                config=service['config']('email');message={'personalizations':[{'to':[{'email':payload['to']}]}],'from':{'email':config['SENDGRID_FROM_EMAIL']},'subject':payload['subject'],'content':[{'type':'text/plain','value':payload['body']}]}
                if payload['attachments']:message['attachments']=[dict(content=base64.b64encode(a['data']).decode(),type='application/pdf',filename=a['filename'],disposition='attachment') for a in payload['attachments']]
                app.config.get('STAFF_HTTP',api)('https://api.sendgrid.com/v3/mail/send','POST',message,{'Authorization':'Bearer '+config['SENDGRID_API_KEY']})
            row.status='sent';row.ciphertext=''
        except Exception:row.status='failed';app.logger.warning('Staff email handoff failed; encrypted outbox retained.')
        db.session.commit()
    def dispatch(ids):
        for identity in ids:
            try:process_mail(identity)
            except Exception:db.session.rollback();app.logger.warning('Staff delivery retry retained.')
    def issue_code(member,purpose,binding):
        limit('staff-code:'+str(member.id)+':'+str(request.remote_addr),5,15)
        code=f'{secrets.randbelow(1000000):06d}';challenge=Challenge(id=secrets.token_hex(16),member_id=member.id,purpose=purpose,binding=binding,revision=member.revision,digest='',expires_at=now()+timedelta(minutes=10))
        challenge.digest=mac(challenge.id+':'+code);db.session.add(challenge);db.session.flush()
        row=mail(member,'security','code:'+challenge.id,member.email,'LionTech staff verification code',f'Your staff verification code is {code}. It expires in 10 minutes. If you did not request it, contact the academy administrator.',challenge=challenge.id)
        db.session.commit();session['staff_challenge']=challenge.id;dispatch([row.id]);return challenge
    def consume_code(purpose,binding):
        row=db.session.scalar(select(Challenge).where(Challenge.id==session.get('staff_challenge')).with_for_update().execution_options(populate_existing=True))
        member=db.session.get(Member,row.member_id) if row else None
        if not row or not member or row.purpose!=purpose or row.binding!=binding or row.used or row.attempts>=5 or aware(row.expires_at)<=now() or row.revision!=member.revision:abort(400,'This code expired or was already used. Request a new code.')
        if not hmac.compare_digest(row.digest,mac(row.id+':'+request.form.get('code','').strip())):
            row.attempts+=1;db.session.commit();abort(400,'The verification code is incorrect.')
        row.used=True;return member
    def start_session(member):
        session.clear();session.update(staff_id=member.id,staff_revision=member.revision,ui_timezone=member.timezone,staff_stepup_until=(now()+timedelta(minutes=5)).timestamp());session.permanent=True;g.staff=member
    def password():
        value=request.form.get('password','')
        if not 12<=len(value)<=128 or value!=request.form.get('confirm_password'):abort(400,'Use matching passwords of 12–128 characters.')
        return generate_password_hash(value,method='pbkdf2:sha256:600000')
    def invite_row(token):
        row=db.session.scalar(select(Invite).where(Invite.digest==hashlib.sha256(token.encode()).hexdigest()).with_for_update().execution_options(populate_existing=True))
        if not row or row.used or row.revoked or aware(row.expires_at)<=now():abort(410,'This invitation expired, was revoked or was already accepted. Request a new invitation.')
        return row
    def create_invitation(member):
        for old in db.session.scalars(select(Invite).where(Invite.member_id==member.id,Invite.used.is_(False))):old.revoked=True
        token=secrets.token_urlsafe(32);inv=Invite(member_id=member.id,digest=hashlib.sha256(token.encode()).hexdigest(),expires_at=now()+timedelta(hours=72));db.session.add(inv);db.session.flush()
        row=mail(member,'invitation','invite:'+inv.id,member.email,'You are invited to LionTech Academy staff',f'Hello {member.name},\nYou have been invited as {ROLES[member.role]} in {member.department}.\nAccept your invitation: {ORIGIN}/staff/invitations/{token}\nThis link expires in 72 hours. Your role is assigned by the administrator. Verify your work email to activate your account.',invite=inv.id)
        return row.id
    @app.route('/staff/invitations/<token>',methods=['GET','POST'])
    def staff_accept(token):
        if session.get('admin') or getattr(g,'staff',None) or g.student:abort(400,'Sign out before accepting a staff invitation.')
        inv=invite_row(token);member=db.session.get(Member,inv.member_id)
        if request.method=='POST':
            if request.form.get('action')=='code':issue_code(member,'accept',inv.id);flash('A verification code was requested for your invited work email.');return redirect(request.path)
            member=consume_code('accept',inv.id)
            if request.form.get('policy_acknowledged')!='yes' or request.form.get('policy_version')!=POLICY_VERSION:abort(400,'Accept the current terms and privacy policy before activating your account.')
            member.password_hash=password();member.policy_version=POLICY_VERSION;member.policy_accepted_at=now();member.active=True;member.verified_at=now();inv.used=True;audit('invitation_accepted',member);db.session.commit();start_session(member);return redirect(url_for('staff_home'))
        return render_template('staff_auth.html',mode='accept',member=member,policy_version=POLICY_VERSION)
    @app.route('/staff/sign-in',methods=['GET','POST'])
    def staff_login():
        if getattr(g,'staff',None):return redirect(url_for('staff_home'))
        if session.get('admin') or g.student:abort(400,'Sign out of your current account before using Staff sign in.')
        if request.method=='POST':
            limit('staff-login:'+str(request.remote_addr),10,15);identity=request.form.get('identity','').strip().casefold();limit('staff-identity:'+identity[:254],20,15)
            member=db.session.scalar(select(Member).where(Member.email==identity))
            if identity.upper().startswith('LTA-STF-') and identity[8:].isdigit():member=db.session.get(Member,int(identity[8:]))
            if not member or not member.active or not member.verified_at or not check_password_hash(member.password_hash,request.form.get('password','')[:129]):
                flash('Sign-in could not be completed. Check your staff credentials and account status.');return redirect(url_for('staff_login'))
            issue_code(member,'login','login');session['staff_verification_mode']='login';return redirect(url_for('staff_verify'))
        return render_template('staff_auth.html',mode='login')
    @app.route('/staff/verify',methods=['GET','POST'])
    def staff_verify():
        purpose=session.get('staff_verification_mode')
        if purpose not in ('login','stepup','reset'):abort(400)
        if request.method=='POST':
            member=consume_code(purpose,purpose)
            if not member.active or not member.verified_at:abort(403)
            if purpose=='reset':member.password_hash=password();member.revision+=1;audit('password_reset',member);db.session.commit();session.clear();flash('Password updated. Previous sessions have ended. Sign in again.');return redirect(url_for('staff_login'))
            if purpose=='stepup':
                if session.get('staff_id')!=member.id:abort(403)
                db.session.commit();session['staff_stepup_until']=(now()+timedelta(minutes=5)).timestamp();return redirect(session.pop('staff_secure_return',url_for('staff_profile',tab='payroll')))
            db.session.commit();start_session(member);return redirect(url_for('staff_home'))
        return render_template('staff_auth.html',mode='verify',purpose=purpose)
    @app.route('/staff/recover',methods=['GET','POST'])
    def staff_recover():
        if request.method=='POST':
            limit('staff-recover:'+str(request.remote_addr),5,15);email=request.form.get('email','').strip().casefold();member=db.session.scalar(select(Member).where(Member.email==email))
            if member and member.active and member.verified_at and request.form.get('staff_number')==member.staff_number:issue_code(member,'reset','reset')
            else:session.pop('staff_challenge',None)
            session['staff_verification_mode']='reset';flash('If the active staff details match, a verification code has been requested.');return redirect(url_for('staff_verify'))
        return render_template('staff_auth.html',mode='recover')
    @app.post('/staff/secure-access')
    @member_required
    def staff_secure_access():
        limit('staff-stepup:'+str(g.staff.id),5,15)
        if not check_password_hash(g.staff.password_hash,request.form.get('password','')[:129]):abort(403,'Confirm your current staff password.')
        destination=request.form.get('return_to','')
        session['staff_secure_return']=destination if destination.startswith('/staff/') and '\\' not in destination and not any(c in destination for c in ('\r','\n')) else url_for('staff_profile',tab='payroll')
        issue_code(g.staff,'stepup','stepup');session['staff_verification_mode']='stepup';return redirect(url_for('staff_verify'))
    def invitation_status(member):
        inv=db.session.scalar(select(Invite).where(Invite.member_id==member.id).order_by(Invite.created_at.desc()))
        return 'Revoked' if inv and inv.revoked else 'Expired' if inv and aware(inv.expires_at)<=now() else 'Invited' if inv and not inv.used else 'Not invited'
    def team_members():return list(db.session.scalars(select(Member).order_by(Member.name)))
    def validate_assignment(member):
        role=request.form.get('role','staff');scope=request.form.get('scope','assigned');sup=request.form.get('supervisor_id','')
        if role not in ROLES or scope not in SCOPES:abort(400)
        supervisor=db.session.get(Member,int(sup)) if sup.isascii() and sup.isdigit() else None
        if sup and (not supervisor or not supervisor.active or supervisor.role!='supervisor' or supervisor.id==member.id):abort(400,'Choose an active supervisor or the portal administrator.')
        seen={member.id};current=supervisor
        while current:
            if current.id in seen:abort(400,'Supervisor assignments cannot form a cycle.')
            seen.add(current.id);current=db.session.get(Member,current.supervisor_id) if current.supervisor_id else None
        member.role=role;member.scope=scope;member.supervisor_id=supervisor.id if supervisor else None
    @app.route('/admin/team',methods=['GET','POST'])
    @h['admin_required']
    def staff_team():
        if request.method=='POST':
            service['reauthenticate']();email=request.form.get('email','').strip().casefold();name=request.form.get('name','').strip();department=request.form.get('department','').strip()
            if len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or not 1<=len(name)<=100 or not 1<=len(department)<=80:abort(400,'Enter a valid work email, full name and department.')
            if db.session.scalar(select(Member.id).where(Member.email==email)):abort(409,'This work email already has a staff record. Resend its invitation or manage the existing account.')
            member=Member(email=email,name=name,department=department);db.session.add(member);db.session.flush();validate_assignment(member);identity=create_invitation(member);audit('staff_invited',member,'Role: '+member.role+'; scope: '+member.scope);db.session.commit();dispatch([identity]);flash('Invitation saved. Check email delivery status below.');return redirect(url_for('staff_team'))
        members=team_members();q=request.args.get('q','').casefold();dept=request.args.get('department','');visible=[s for s in members if (not q or q in (s.name+' '+s.email+' '+s.department+' '+ROLES[s.role]).casefold()) and (not dept or s.department==dept)]
        return render_template('staff_team.html',admin_view='team',members=visible,all_members=members,invites=list(db.session.scalars(select(Invite).order_by(Invite.created_at.desc()))),mail_rows=list(db.session.scalars(select(Mail).where(Mail.kind=='invitation').order_by(Mail.created_at.desc()).limit(30))),audit_rows=list(db.session.scalars(select(Audit).order_by(Audit.created_at.desc()).limit(40))),stats=dict(active=sum(s.active for s in members),pending=sum(not s.verified_at and invitation_status(s)=='Invited' for s in members),review=db.session.scalar(select(db.func.count()).select_from(Report).where(Report.status=='submitted'))))
    @app.post('/admin/team/<int:member_id>/manage')
    @h['admin_required']
    def staff_manage(member_id):
        service['reauthenticate']();member=db.get_or_404(Member,member_id);action=request.form.get('action');identities=[]
        if action=='resend':
            if member.verified_at:abort(400,'This account already accepted its invitation.')
            identities.append(create_invitation(member))
        elif action in ('suspend','reactivate','revoke'):
            if action=='reactivate' and not member.verified_at:abort(400,'Accept the invitation before reactivating staff sign-in.')
            member.active=action=='reactivate';member.revision+=1
            if action in ('suspend','revoke'):
                for inv in db.session.scalars(select(Invite).where(Invite.member_id==member.id,Invite.used.is_(False))):inv.revoked=True
        elif action=='permissions':
            validate_assignment(member);member.revision+=1;department=request.form.get('department','').strip()
            if not 1<=len(department)<=80:abort(400)
            member.department=department
            db.session.query(Grant).filter_by(reviewer_id=member.id).delete(synchronize_session=False)
            for identity in set(request.form.getlist('assigned_member')):
                if not identity.isdigit() or not db.session.get(Member,int(identity)):abort(400)
                db.session.add(Grant(reviewer_id=member.id,member_id=int(identity)))
        else:abort(400)
        audit('staff_'+action,member,'Role: '+member.role+'; scope: '+member.scope+'; department: '+member.department+'; supervisor: '+str(member.supervisor_id)+'; active: '+str(member.active));db.session.commit();dispatch(identities);flash('Staff update saved. Permission or status changes invalidate existing sessions.');return redirect(url_for('staff_team'))
    def accessible(kind):
        return [s for s in team_members() if can(s,kind)]
    @app.get('/staff')
    @member_required
    def staff_home():
        return render_template('staff_home.html',tasks=list(db.session.scalars(select(Task).where(Task.member_id==g.staff.id).order_by(Task.due_on))),reports=list(db.session.scalars(select(Report).where(Report.member_id==g.staff.id).order_by(Report.report_date.desc()).limit(10))))
    @app.route('/staff/tasks',methods=['GET','POST'])
    def staff_tasks():
        if not session.get('admin') and not getattr(g,'staff',None):return redirect(url_for('staff_login'))
        people=accessible('tasks') if not session.get('admin') else team_members()
        if request.method=='POST':
            target=db.get_or_404(Member,request.form.get('member_id',type=int))
            if not can(target,'tasks') or (not session.get('admin') and g.staff.role!='supervisor') or not target.active:abort(403)
            title=request.form.get('title','').strip();description=request.form.get('description','').strip();priority=request.form.get('priority','normal')
            if not 1<=len(title)<=160 or len(description)>5000 or priority not in ('normal','high','urgent'):abort(400)
            try:due=date.fromisoformat(request.form.get('due_on',''))
            except ValueError:abort(400,'Choose a due date.')
            task=Task(member_id=target.id,title=title,description=description,due_on=due,priority=priority);db.session.add(task);db.session.flush();audit('task_assigned',target,'Task '+str(task.id));row=mail(target,'task','task:'+str(task.id),target.email,'New LionTech staff task',f'{task.title}\nDue: {task.due_on}\nView your assigned task: {ORIGIN}/staff/tasks');db.session.commit();dispatch([row.id]);return redirect(url_for('staff_tasks'))
        return render_template('staff_tasks.html',people=people,tasks=list(db.session.scalars(select(Task).where(Task.member_id.in_([s.id for s in people])).order_by(Task.due_on))))
    @app.post('/staff/tasks/<int:task_id>')
    @member_required
    def staff_task_update(task_id):
        task=db.get_or_404(Task,task_id)
        if task.member_id!=g.staff.id:abort(403)
        status=request.form.get('status');result=request.form.get('result','').strip()
        if status not in ('assigned','in_progress','completed') or len(result)>5000 or (status=='completed' and not result):abort(400,'Record the outcome before marking a task completed.')
        task.status=status;task.result=result;task.updated_at=now();audit('task_'+status,g.staff,'Task '+str(task.id));db.session.commit();return redirect(url_for('staff_tasks'))
    @app.route('/staff/reports',methods=['GET','POST'])
    @member_required
    def staff_reports():
        if request.method=='POST':
            try:day=date.fromisoformat(request.form.get('report_date',''))
            except ValueError:abort(400,'Choose a report date.')
            today=now().astimezone(ZoneInfo(g.staff.timezone)).date()
            if not today-timedelta(days=366)<=day<=today:abort(400,'Reports must use today or a date in the past year.')
            report=db.session.scalar(select(Report).where(Report.member_id==g.staff.id,Report.report_date==day).with_for_update())
            if not report:
                report=Report(member_id=g.staff.id,report_date=day);db.session.add(report)
                try:db.session.flush()
                except IntegrityError:db.session.rollback();abort(409,'A draft was created in another tab. Refresh before continuing.')
            if report.status not in ('draft','revision_requested'):abort(409,'This report has been submitted. A supervisor must request a revision before a new version can be submitted.')
            values={key:request.form.get(key,'').strip() for key,_ in FIELDS}
            if any(len(v)>5000 for v in values.values()):abort(400,'Each report section supports up to 5,000 characters.')
            report.draft=values;identities=[]
            if request.form.get('action')=='submit':
                if not values['completed'] or not values['outcomes']:abort(400,'Complete the tasks and outcomes sections before submitting.')
                report.latest+=1;report.status='submitted';submitted=now();reference=f'DR-{day:%Y%m%d}-{g.staff.id:04d}-V{report.latest}'
                payload=dict(values,name=g.staff.name,staff_number=g.staff.staff_number,supervisor=supervisor_name(g.staff),date=day.isoformat(),version=report.latest,reference=reference,submitted_at=submitted.isoformat())
                support=[]
                for identity in set(request.form.getlist('supporting_document')):
                    doc=db.session.get(Document,identity)
                    if not doc or doc.member_id!=g.staff.id or doc.kind!='report_support':abort(403)
                    support.append(dict(id=doc.id,name=doc.filename))
                payload['attachments']=support
                version=Version(report_id=report.id,version=report.latest,payload=payload,submitted_at=submitted);db.session.add(version);db.session.flush()
                supervisor=db.session.get(Member,g.staff.supervisor_id) if g.staff.supervisor_id else None
                admin=app.extensions['admin_security']['state']()
                recipients=[(g.staff,g.staff.email),(supervisor,supervisor.email if supervisor and supervisor.active and supervisor.verified_at else admin.email if not g.staff.supervisor_id else '')]
                for recipient,email in recipients:
                    if email:identities.append(mail(recipient,'report',f'report:{version.id}:{email}',email,'Daily staff report — '+day.isoformat(),f'{g.staff.name} submitted daily report {reference}.\nA PDF copy is attached. The report and version history remain available in the portal: {ORIGIN}/staff/reports/{report.id}',version=version.id).id)
                audit('daily_report_submitted',g.staff,reference)
            else:audit('daily_report_draft_saved',g.staff,str(day))
            try:db.session.commit()
            except IntegrityError:db.session.rollback();abort(409,'This report was updated in another tab. Refresh before trying again.')
            dispatch(identities);flash('Daily report submitted and email delivery recorded.' if report.status=='submitted' else 'Draft saved.');return redirect(url_for('staff_report_detail',report_id=report.id))
        reports=list(db.session.scalars(select(Report).where(Report.member_id==g.staff.id).order_by(Report.report_date.desc())));editing=None
        if request.args.get('edit',type=int):
            editing=db.get_or_404(Report,request.args['edit'])
            if editing.member_id!=g.staff.id:abort(403)
        return render_template('staff_reports.html',reports=reports,editing=editing,today=now().astimezone(ZoneInfo(g.staff.timezone)).date(),fields=FIELDS,support=list(db.session.scalars(select(Document).where(Document.member_id==g.staff.id,Document.kind=='report_support'))))
    @app.get('/staff/review')
    def staff_review_queue():
        if not session.get('admin') and not getattr(g,'staff',None):return redirect(url_for('staff_login'))
        people=team_members() if session.get('admin') else accessible('reports')
        if not session.get('admin') and g.staff.role!='supervisor':abort(403)
        return render_template('staff_review_queue.html',reports=list(db.session.scalars(select(Report).where(Report.member_id.in_([s.id for s in people]),Report.status.in_(('submitted','revision_requested'))).order_by(Report.report_date.desc()))))
    @app.route('/staff/reports/<int:report_id>',methods=['GET','POST'])
    def staff_report_detail(report_id):
        if not session.get('admin') and not getattr(g,'staff',None):return redirect(url_for('staff_login'))
        report=db.get_or_404(Report,report_id);owner=db.session.get(Member,report.member_id)
        if not can(owner,'reports') or (not report.latest and (not getattr(g,'staff',None) or g.staff.id!=owner.id)):abort(403)
        versions=list(db.session.scalars(select(Version).where(Version.report_id==report.id).order_by(Version.version.desc())))
        if request.method=='POST':
            if not session.get('admin') and (g.staff.id==owner.id or g.staff.role!='supervisor'):abort(403)
            report=db.session.scalar(select(Report).where(Report.id==report.id).with_for_update().execution_options(populate_existing=True));decision=request.form.get('decision');comment=request.form.get('comment','').strip()
            if report.status!='submitted' or not versions or versions[0].version!=report.latest or request.form.get('version_id',type=int)!=versions[0].id:abort(409,'Review the latest submitted version.')
            if decision not in ('reviewed','revision_requested') or not 1<=len(comment)<=2000:abort(400,'Add a review comment.')
            db.session.add(Review(version_id=versions[0].id,reviewer=actor(),decision=decision,comment=comment));report.status=decision;audit('report_'+decision,owner,versions[0].payload['reference']);row=mail(owner,'review',f'review:{versions[0].id}:{decision}',owner.email,'Daily report review update',f'Your daily report {report.report_date} is {decision.replace("_"," ")}.\n{comment}\nView the report: {ORIGIN}/staff/reports/{report.id}');db.session.commit();dispatch([row.id]);return redirect(request.path)
        return render_template('staff_report_detail.html',report=report,owner=owner,versions=versions,reviews=list(db.session.scalars(select(Review).where(Review.version_id.in_([v.id for v in versions])).order_by(Review.created_at.desc()))),mail_rows=list(db.session.scalars(select(Mail).where(or_(*[Mail.event_key.like('report:'+str(v.id)+':%') for v in versions]) if versions else db.false()).order_by(Mail.created_at.desc()))),fields=FIELDS)
    @app.get('/staff/reports/<int:report_id>/draft.pdf')
    @member_required
    def staff_draft_pdf(report_id):
        report=db.get_or_404(Report,report_id)
        if report.member_id!=g.staff.id or report.status not in ('draft','revision_requested'):abort(403)
        payload=dict(report.draft,name=g.staff.name,staff_number=g.staff.staff_number,supervisor=supervisor_name(g.staff),date=report.report_date.isoformat(),version='DRAFT',reference='DRAFT-'+str(report.id),submitted_at='Not submitted')
        return Response(report_pdf(payload),mimetype='application/pdf',headers={'Content-Disposition':'inline; filename="daily-report-draft.pdf"'})
    @app.get('/staff/reports/versions/<int:version_id>.pdf')
    def staff_report_pdf(version_id):
        version=db.get_or_404(Version,version_id);report=db.session.get(Report,version.report_id);owner=db.session.get(Member,report.member_id)
        if not can(owner,'reports'):abort(403)
        audit('report_pdf_downloaded',owner,version.payload['reference']);db.session.commit()
        return Response(report_pdf(version.payload),mimetype='application/pdf',headers={'Content-Disposition':'attachment; filename="'+version.payload['reference']+'.pdf"'})
    @app.route('/staff/profile',methods=['GET','POST'])
    @member_required
    def staff_profile():
        personal=db.session.get(Private,(g.staff.id,'personal'));payroll=db.session.get(Private,(g.staff.id,'payroll'));data={};bank={}
        if request.method=='POST':
            kind=request.form.get('kind','personal')
            if kind not in ('personal','payroll'):abort(400)
            if kind=='payroll':stepup()
            keys=('phone','address','emergency_contact') if kind=='personal' else ('country','holder','bank','institution','transit','account','routing','iban','swift')
            values={key:request.form.get(key,'').strip() for key in keys}
            if any(len(v)>500 for v in values.values()):abort(400)
            if kind=='payroll':
                if values['country'] not in ('Canada','United States','Other') or not all(values[k] for k in ('holder','bank','account')):abort(400,'Complete the bank country, holder, bank and account fields.')
                if values['country']=='Canada' and (not re.fullmatch(r'\d{3}',values['institution']) or not re.fullmatch(r'\d{5}',values['transit'])):abort(400,'Canadian institution and transit numbers require 3 and 5 digits.')
                if values['country']=='United States' and not re.fullmatch(r'\d{9}',values['routing']):abort(400,'Enter the 9-digit US routing number.')
            row=db.session.get(Private,(g.staff.id,kind)) or Private(member_id=g.staff.id,kind=kind,ciphertext='');row.ciphertext=service['seal']('staff-'+kind,str(g.staff.id),values);row.status='submitted' if request.form.get('action')=='submit' else 'draft';row.review_note='';row.updated_at=now();db.session.add(row);audit(kind+'_updated',g.staff);db.session.commit();return redirect(url_for('staff_profile',tab=kind))
        try:
            if personal:data=service['unseal']('staff-personal',str(g.staff.id),personal.ciphertext)
            if payroll and session.get('staff_stepup_until',0)>=now().timestamp():bank=service['unseal']('staff-payroll',str(g.staff.id),payroll.ciphertext)
        except ValueError:flash('Private profile storage is unavailable. Contact an authorized HR or payroll colleague.')
        return render_template('staff_profile.html',personal=data,bank=bank,personal_record=personal,payroll_record=payroll,documents=list(db.session.scalars(select(Document).where(Document.member_id==g.staff.id).order_by(Document.created_at.desc()))))
    @app.post('/staff/documents')
    @member_required
    def staff_document_upload():
        kind=request.form.get('kind');upload=request.files.get('file')
        if kind not in DOCS or not upload:abort(400)
        if kind in ('government_id','bank_confirmation'):stepup()
        filename=secure_filename(upload.filename or '')[:180];ext=filename.rsplit('.',1)[-1].lower();mime={'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','txt':'text/plain'}.get(ext);data=upload.read(2*1024*1024+1)
        if not filename or not mime or not 0<len(data)<=2*1024*1024:abort(400,'Use a PDF, JPG, PNG, DOCX or text file up to 2 MB.')
        if ext=='pdf' and not data.startswith(b'%PDF-'):abort(400,'Upload a valid PDF.')
        if mime.startswith('image/'):
            from file_storage import validate_image
            try:validate_image(data,mime)
            except ValueError:abort(400,'Upload a valid image.')
        if ext=='docx':
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    if '[Content_Types].xml' not in archive.namelist() or sum(i.file_size for i in archive.infolist())>50*1024*1024:raise ValueError()
            except (zipfile.BadZipFile,ValueError):abort(400,'Upload a valid DOCX file.')
        row=Document(id=secrets.token_hex(16),member_id=g.staff.id,kind=kind,filename=filename,mime=mime,pathname='');encrypted=service['seal']('staff-document',row.id,dict(content=base64.b64encode(data).decode()));row.pathname=app.config['FILE_STORAGE'].put('staff-encrypted/'+row.id,encrypted.encode());db.session.add(row);audit('document_uploaded',g.staff,kind);db.session.commit();return redirect(url_for('staff_profile',tab='documents'))
    @app.get('/staff/documents/<document_id>')
    def staff_document_download(document_id):
        row=db.get_or_404(Document,document_id);owner=db.session.get(Member,row.member_id)
        if not can(owner,row.kind):abort(403)
        if row.kind in ('government_id','bank_confirmation'):stepup()
        values=service['unseal']('staff-document',row.id,app.config['FILE_STORAGE'].get(row.pathname).decode());audit('document_downloaded',owner,row.kind);db.session.commit()
        return Response(base64.b64decode(values['content']),mimetype=row.mime,headers={'Content-Disposition':'attachment; filename="'+row.filename+'"'})
    @app.route('/staff/personnel/<int:member_id>/<kind>',methods=['GET','POST'])
    @member_required
    def staff_personnel(member_id,kind):
        owner=db.get_or_404(Member,member_id)
        if kind not in ('personal','payroll') or not can(owner,kind):abort(403)
        if kind=='payroll':stepup()
        row=db.session.get(Private,(owner.id,kind))
        if request.method=='POST':
            if owner.id==g.staff.id or not row or request.form.get('decision') not in ('approved','changes_requested'):abort(403)
            comment=request.form.get('comment','').strip()
            if not 1<=len(comment)<=1000:abort(400)
            row.status=request.form['decision'];row.review_note=comment;audit(kind+'_reviewed',owner,row.status);db.session.commit();return redirect(request.path)
        values=service['unseal']('staff-'+kind,str(owner.id),row.ciphertext) if row else {};audit(kind+'_viewed',owner);db.session.commit()
        return render_template('staff_personnel.html',owner=owner,kind=kind,values=values,record=row,documents=[doc for doc in db.session.scalars(select(Document).where(Document.member_id==owner.id)) if can(owner,doc.kind)])
    @app.get('/staff/people')
    @member_required
    def staff_people():
        kind='payroll' if g.staff.role=='payroll' else 'personal' if g.staff.role=='hr' else 'reports'
        if g.staff.role not in ('hr','payroll','supervisor'):abort(403)
        return render_template('staff_people.html',people=accessible(kind),kind=kind)
    @app.get('/staff/notifications')
    @member_required
    def staff_notifications():
        return render_template('staff_notifications.html',rows=list(db.session.scalars(select(Mail).where(Mail.member_id==g.staff.id).order_by(Mail.created_at.desc()).limit(50))),events=list(db.session.scalars(select(Audit).where(Audit.member_id==g.staff.id).order_by(Audit.created_at.desc()).limit(30))))
    @app.post('/staff/mail/<mail_id>/retry')
    def staff_mail_retry(mail_id):
        row=db.get_or_404(Mail,mail_id)
        if not session.get('admin') and (not getattr(g,'staff',None) or row.member_id!=g.staff.id):abort(403)
        if row.kind=='security':abort(400,'Request a new code instead of retrying an old security email.')
        if row.status not in ('failed','pending'):abort(400)
        if row.attempts>=5:abort(400,'The retry limit was reached. Contact portal support.')
        dispatch([row.id]);return redirect(url_for('staff_team') if session.get('admin') else url_for('staff_notifications'))
    def drain():
        identities=list(db.session.scalars(select(Mail.id).where(Mail.status.in_(('pending','failed')),Mail.attempts<5).order_by(Mail.created_at).limit(30)));dispatch(identities);return len(identities)
    @app.context_processor
    def context():return dict(staff_roles=ROLES,staff_invitation_status=invitation_status,staff_scopes=SCOPES,staff_doc_types=DOCS,staff_fields=FIELDS,staff_member=lambda identity:db.session.get(Member,identity),staff_supervisor=supervisor_name,staff_can=can,staff_assigned=lambda reviewer,member:db.session.get(Grant,(reviewer,member)) is not None,staff_secure=bool(getattr(g,'staff',None) and session.get('staff_stepup_until',0)>=now().timestamp()))
    @app.after_request
    def headers(response):
        if request.path.startswith(('/staff','/admin/team')):response.headers['Referrer-Policy']='no-referrer';response.headers['Cache-Control']='no-store'
        return response
    app.extensions['staff_portal']=dict(drain=drain,process_mail=process_mail,can=can,scoped=scoped)
