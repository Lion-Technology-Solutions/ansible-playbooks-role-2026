"""Administrator-linked identities and role-isolated, revocable profile switching."""
import hashlib,hmac,re,secrets
from datetime import timedelta
from flask import abort,flash,g,redirect,render_template,request,session,url_for,current_app,has_request_context
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash


@event.listens_for(Session,'before_flush')
def stamp_linked_administrator_audits(session_,context,instances):
    if has_request_context() and 'member_profiles' in current_app.extensions:current_app.extensions['member_profiles']['stamp_audit'](session_)


class StaffPersona:
    def __init__(self,member,role):self._member=member;self.role=role
    def __getattr__(self,key):return getattr(self._member,key)


def define(db):
    class MemberPerson(db.Model):
        __tablename__='portal_member_people'
        id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
        name=db.Column(db.String(100),nullable=False)
        revision=db.Column(db.Integer,nullable=False,default=1)
    class ProfileMembership(db.Model):
        __tablename__='portal_profile_memberships'
        actor=db.Column(db.String(80),primary_key=True)
        person_id=db.Column(db.String(32),db.ForeignKey('portal_member_people.id'),nullable=False,index=True)
        __table_args__=(db.UniqueConstraint('person_id','kind'),)
        kind=db.Column(db.String(15),nullable=False)
    class ExtraProfileRole(db.Model):
        __tablename__='portal_extra_profile_roles'
        person_id=db.Column(db.String(32),db.ForeignKey('portal_member_people.id'),primary_key=True)
        role=db.Column(db.String(20),primary_key=True)
    class ProfileHandle(db.Model):
        __tablename__='portal_profile_handles'
        owner=db.Column(db.String(80),primary_key=True)
        username=db.Column(db.String(40),nullable=False,unique=True)
    return dict(MemberPerson=MemberPerson,ProfileMembership=ProfileMembership,ExtraProfileRole=ExtraProfileRole,ProfileHandle=ProfileHandle)


def install(app,db,m):
    Person,Membership,Role,Handle=(m[k] for k in ('MemberPerson','ProfileMembership','ExtraProfileRole','ProfileHandle'))
    Staff,Student=m['StaffMember'],m['Student'];auth=app.extensions['auth_security'];service=app.extensions['integrations']
    def now():return app.config.get('SESSION_CLOCK',m['now'])()
    def actor():return 'admin:1' if session.get('admin') else 'staff:'+str(g.staff.id) if g.staff else 'student:'+g.student.id if g.student else None
    def account(identity):
        try:kind,key=identity.split(':',1)
        except (AttributeError,ValueError):abort(400)
        if kind=='admin' and key=='1':return app.extensions['admin_security']['state']()
        if kind=='student':return db.get_or_404(Student,key)
        if kind=='staff' and key.isdecimal():return db.get_or_404(Staff,int(key))
        abort(400)
    def membership(identity):return db.session.get(Membership,identity)
    def person(identity):
        link=membership(identity)
        return db.session.get(Person,link.person_id) if link else None
    def members(p):return list(db.session.scalars(select(Membership).where(Membership.person_id==p.id))) if p else []
    def extra(p,role):return bool(p and db.session.get(Role,(p.id,role)))
    def available(p):
        choices=[];links=members(p)
        for link in links:
            row=account(link.actor)
            if link.kind=='student':
                control=db.session.get(m['StudentControl'],row.id)
                active=not (control and not control.active) and not db.session.get(m['StudentArchive'],row.id)
                choices.append(dict(key=link.actor,label='Student',name=row.name,active=active))
            elif link.kind=='staff':
                active=bool(row.active and row.verified_at)
                choices.append(dict(key=link.actor,label={'staff':'Team member','supervisor':'Supervisor','instructor':'Instructor','hr':'HR','payroll':'Payroll'}[row.role],name=row.name,active=active))
                if row.role!='instructor' and extra(p,'instructor'):choices.append(dict(key='instructor:'+str(row.id),label='Instructor',name=row.name,active=active))
                if extra(p,'administrator'):choices.append(dict(key='administrator:'+str(row.id),label='Administrator',name=row.name,active=active))
            elif link.kind=='admin':choices.append(dict(key='admin:1',label='Administrator',name=p.name,active=True))
        return choices
    def source_snapshot():
        identity=actor()
        if not identity:abort(403)
        values=dict(actor=identity)
        if identity=='admin:1':values['stamp']=app.extensions['admin_security']['stamp']()
        elif identity.startswith('staff:'):values.update(revision=g.staff.revision,stamp=hashlib.sha256(g.staff.password_hash.encode()).hexdigest())
        else:
            row=auth['current']()
            if not row:abort(403)
            values.update(token=session.get('auth_session'),stamp=row.stamp,version=row.version)
        return values
    def valid_source(source):
        identity=source.get('actor','')
        if identity=='admin:1':return hmac.compare_digest(source.get('stamp',''),app.extensions['admin_security']['stamp']())
        if identity.startswith('staff:'):
            key=identity.split(':',1)[1];row=db.session.get(Staff,int(key)) if key.isdecimal() else None
            return bool(row and row.active and row.verified_at and row.revision==source.get('revision') and hashlib.sha256(row.password_hash.encode()).hexdigest()==source.get('stamp'))
        if identity.startswith('student:'):
            row=auth['valid_login'](source.get('token'))
            return bool(row and 'student:'+row.student_id==identity and row.stamp==source.get('stamp') and row.version==source.get('version') and not db.session.get(m['StudentArchive'],row.student_id))
        return False
    def clear():session.clear();g.student=None;g.staff=None
    def guard():
        context=session.get('linked_profiles')
        if context:
            p=db.session.get(Person,context.get('person'))
            source=context.get('source',{});source_link=membership(source.get('actor',''))
            if (not actor() and now().timestamp()>=context.get('pending_until',0)) or not p or p.revision!=context.get('revision') or not source_link or source_link.person_id!=p.id or not valid_source(source) or now().timestamp()>=context.get('expires',0):
                clear();return redirect(url_for('login',reason='profile-access-changed'))
            target=context.get('active')
            if target and not any(c['key']==target and c['active'] for c in available(p)):
                clear();return redirect(url_for('login',reason='profile-access-changed'))
            if session.get('admin') and target:
                delegated='staff:'+target.split(':')[1] if target.startswith('administrator:') else source.get('actor') if source.get('actor')!='admin:1' else None
                if delegated:g.linked_admin=account(delegated);g.linked_admin_actor=delegated
                g.member_person=p
                # Shared recovery credentials can only be changed after native administrator sign-in.
                if delegated and request.endpoint in ('admin_profile','admin_password_verify','security_settings','chatgpt_settings') and request.method=='POST':abort(403,'Use the original administrator identity for shared portal security and credential changes.')
            elif g.staff:
                if target and target.startswith('instructor:'):g.staff=StaffPersona(g.staff,'instructor')
                g.member_person=p
            if g.student:g.member_person=p
        elif actor():g.member_person=person(actor())
    def contact_owner():
        member=getattr(g,'linked_admin',None)
        return getattr(g,'linked_admin_actor','admin:1') if member else 'admin:1'
    def confirmation_password(value):
        member=getattr(g,'linked_admin',None)
        if member:
            from werkzeug.security import check_password_hash
            return check_password_hash(member.password_hash,value)
        return app.extensions['admin_security']['check'](value)
    def audit(action,detail):
        db.session.add(m['StaffAudit'](actor=actor() or 'anonymous',member_id=g.staff.id if g.staff else None,action=action,detail=detail[:1000]))
    def privileged_ready(target,member,p):
        context=session.get('linked_profiles',{})
        source=context.get('source',{}).get('actor',actor())
        if target==source:return True
        if member and source=='staff:'+str(member.id) and g.staff and g.staff.id==member.id and session.get('staff_stepup_until',0)>now().timestamp():return True
        proof=session.get('profile_stepup',{})
        return proof.get('target')==target and proof.get('person_revision')==p.revision and proof.get('until',0)>now().timestamp()
    def request_profile_code(target,p):
        from types import SimpleNamespace
        auth['limit']('profile-switch:'+actor(),5,15)
        kind,key=target.split(':',1)
        destination=account(('staff:' if kind in ('instructor','administrator') else kind+':')+key)
        session['profile_target']=target;session['profile_binding']=secrets.token_hex(24)
        code=f'{secrets.randbelow(1000000):06d}'
        values=dict(target=target,person=p.id,revision=p.revision,code=hashlib.sha256(code.encode()).hexdigest())
        challenge,_=auth['challenge'](None,'linked_profile_switch',session['profile_binding'],values,10)
        session['profile_challenge']=challenge.id
        delivery=auth['send_mail'](SimpleNamespace(email=destination.email),'Verify your LionTech profile switch',f'Your profile-switch code is {code}. It expires in 10 minutes. This authorizes opening your {kind} profile. Never share this code.',challenge_id=challenge.id)
        if delivery.status=='failed':flash('The verification email could not be handed to the email provider. Check email delivery and retry.');return redirect(url_for('member_profile_switcher'))
        return redirect(url_for('profile_switch_verify'))
    def activate(target):
        identity=actor()
        if not identity:abort(403)
        p=person(session.get('linked_profiles',{}).get('source',{}).get('actor',identity))
        choices=available(p)
        if not p or not any(c['key']==target and c['active'] for c in choices):abort(403,'This profile is not assigned or is inactive.')
        kind,key=target.split(':',1)
        member=db.get_or_404(Staff,int(key)) if kind in ('staff','instructor','administrator') else None
        if not privileged_ready(target,member,p):return request_profile_code(target,p)
        if kind=='student' and not db.session.scalar(select(m['PolicyAcceptance'].student_id).where(m['PolicyAcceptance'].student_id==key)):
            session['profile_target']=target;return redirect(url_for('profile_student_consent'))
        context=session.get('linked_profiles')
        old=app.extensions['profile_sessions']['record']()
        if not old:abort(403)
        if not context:context=dict(person=p.id,revision=p.revision,source=source_snapshot(),expires=m['aware'](old.absolute_expires_at).timestamp())
        context['active']=target;context['pending_until']=min(context['expires'],(now()+timedelta(minutes=15)).timestamp())
        student_token=session.get('auth_session') or context.get('student_token')
        proof=session.get('profile_stepup');old.revoked=True
        audit('profile_switched',identity+' -> '+target)
        session.clear();session['linked_profiles']=context
        if proof:session['profile_stepup']=proof
        g.student=None;g.staff=None
        if kind in ('admin','administrator'):
            session.update(admin=True,admin_auth=app.extensions['admin_security']['stamp']());destination='admin'
        elif kind in ('staff','instructor'):
            session.update(staff_id=member.id,staff_revision=member.revision,ui_timezone=member.timezone)
            if proof and proof.get('target') in ('staff:'+str(member.id),'instructor:'+str(member.id),'administrator:'+str(member.id)):session['staff_stepup_until']=proof['until']
            g.staff=StaffPersona(member,'instructor') if kind=='instructor' else member;destination='staff_teaching' if g.staff.role=='instructor' else 'staff_home'
        else:
            student=db.get_or_404(Student,key);login=auth['valid_login'](student_token) if student_token else None
            if login and login.student_id==key:
                session.update(student_id=key,student_auth=login.stamp,auth_session=student_token);g.student=student;destination='portal'
            else:
                db.session.commit();return auth['begin'](student)
        token=secrets.token_urlsafe(32)
        current='admin:1' if session.get('admin') else 'staff:'+str(g.staff.id) if g.staff else 'student:'+g.student.id
        row=m['PortalSessionActivity'](digest=hashlib.sha256(token.encode()).hexdigest(),actor=current,channel=secrets.token_hex(12),last_active_at=now(),absolute_expires_at=datetime_from_timestamp(context['expires']),revoked=False)
        db.session.add(row);db.session.commit();session['portal_session']=token;session.permanent=True
        return redirect(url_for(destination))
    def datetime_from_timestamp(value):
        from datetime import datetime,timezone
        return datetime.fromtimestamp(value,timezone.utc)
    @app.route('/account/profiles',methods=['GET','POST'])
    def member_profile_switcher():
        identity=actor()
        if not identity:abort(403)
        if request.method=='POST':return activate(request.form.get('profile',''))
        session.pop('role_preview',None)
        p=person(session.get('linked_profiles',{}).get('source',{}).get('actor',identity))
        return render_template('member_profile_switcher.html',person=p,profiles=available(p),active_profile=session.get('linked_profiles',{}).get('active',identity))
    @app.route('/account/profiles/verify',methods=['GET','POST'])
    def profile_switch_verify():
        if not actor() or not session.get('profile_target') or not session.get('profile_binding'):abort(403)
        if request.method=='POST':
            auth['limit']('profile-switch-verify:'+actor(),10,15)
            try:challenge,values=auth['load_challenge'](session.get('profile_challenge'),'linked_profile_switch',binding=session['profile_binding'])
            except ValueError as error:abort(400,str(error))
            p=person(session.get('linked_profiles',{}).get('source',{}).get('actor',actor()))
            target=session['profile_target']
            if not p or values.get('target')!=target or values.get('person')!=p.id or values.get('revision')!=p.revision:abort(403)
            if not hmac.compare_digest(values.get('code',''),hashlib.sha256(request.form.get('code','').encode()).hexdigest()):
                challenge.attempts+=1;db.session.commit();abort(400,'The verification code is incorrect.')
            challenge.consumed=True
            session['profile_stepup']=dict(target=target,person_revision=p.revision,until=(now()+timedelta(minutes=5)).timestamp())
            db.session.commit();return activate(target)
        return render_template('member_profile_verify.html')
    @app.route('/account/profiles/student-consent',methods=['GET','POST'])
    def profile_student_consent():
        if not actor() or not session.get('profile_target','').startswith('student:'):abort(403)
        target=session['profile_target'];p=person(actor())
        if not any(c['key']==target and c['active'] for c in available(p)):abort(403)
        if request.method=='POST':
            if request.form.get('accept_terms')!='yes':abort(400,'Accept the student terms and privacy policy to continue.')
            from legal_content import POLICY_VERSION
            db.session.add(m['PolicyAcceptance'](student_id=target.split(':')[1],version=POLICY_VERSION,method='linked_profile'));db.session.commit();return activate(target)
        return render_template('member_student_consent.html')
    @app.route('/admin/member-profiles',methods=['GET','POST'])
    def admin_member_profiles():
        if not session.get('admin'):abort(403)
        if request.method=='POST':
            service['reauthenticate']();anchor=request.form.get('anchor','');row=account(anchor);action=request.form.get('action')
            if request.form.get('confirm_identity')!='yes':abort(400,'Confirm that these profiles belong to the same person.')
            p=person(anchor)
            if not p:p=Person(name=row.name);db.session.add(p);db.session.flush();db.session.add(Membership(actor=anchor,kind=anchor.split(':')[0],person_id=p.id));db.session.flush()
            try:
                if action=='link':
                    target=request.form.get('target','');other=account(target);previous=membership(target)
                    if previous and previous.person_id!=p.id:
                        previous_person=db.session.get(Person,previous.person_id)
                        if len(members(previous_person))!=1 or db.session.scalar(select(Role.role).where(Role.person_id==previous_person.id)):abort(409,'This account has an existing profile group. Detach its additional profiles and responsibilities before attaching it here.')
                        previous_person.revision+=1;db.session.delete(previous);db.session.flush();previous=None
                    if not previous:db.session.add(Membership(actor=target,kind=target.split(':')[0],person_id=p.id))
                elif action=='unlink':
                    target=request.form.get('target','');link=membership(target)
                    if not link or link.person_id!=p.id or target==anchor:abort(400,'Choose an additional profile in this group.')
                    db.session.delete(link)
                elif action=='roles':
                    if not any(link.kind=='staff' for link in members(p)):abort(400,'Link an invited, verified staff profile first.')
                    for role in ('instructor','administrator'):
                        existing=db.session.get(Role,(p.id,role));enabled=request.form.get(role)=='yes'
                        if enabled and not existing:db.session.add(Role(person_id=p.id,role=role))
                        elif not enabled and existing:db.session.delete(existing)
                elif action=='create_student':
                    country=request.form.get('country','')
                    if country not in m['COUNTRIES']:abort(400,'Choose the student residence country.')
                    if db.session.scalar(select(Student.id).where(Student.email==row.email)):abort(409,'A student account already uses this email. Link that existing account explicitly.')
                    student=Student(name=p.name,email=row.email,phone='',country=country,kind='existing',approved=True,password_hash=generate_password_hash(secrets.token_urlsafe(48)))
                    db.session.add(student);db.session.flush();db.session.add(m['StudentIdentity'](student_id=student.id));profile=auth['new_profile'](student)
                    if anchor.startswith('staff:') and row.verified_at:profile.email_verified_at=row.verified_at
                    db.session.add(Membership(actor='student:'+student.id,kind='student',person_id=p.id))
                elif action=='create_staff':
                    if db.session.scalar(select(Staff.id).where(Staff.email==row.email)):abort(409,'A staff account already uses this email. Link it explicitly.')
                    department=request.form.get('department','').strip()
                    if not 1<=len(department)<=80:abort(400,'Enter a department.')
                    staff=Staff(name=p.name,email=row.email,department=department,role='staff',scope='assigned')
                    db.session.add(staff);db.session.flush();db.session.add(Membership(actor='staff:'+str(staff.id),kind='staff',person_id=p.id));mail_id=app.extensions['staff_portal']['create_invitation'](staff)
                    g.profile_invite=mail_id
                elif action=='identity':
                    name=request.form.get('name','').strip();username=request.form.get('username','').strip().casefold()
                    if not 1<=len(name)<=100 or (username and (not re.fullmatch(r'[a-z0-9_][a-z0-9_.-]{2,39}',username) or re.match(r'lt-\d',username))):abort(400,'Use a name up to 100 characters and a valid username of 3–40 characters.')
                    p.name=name
                    for link in members(p):
                        obj=account(link.actor);obj.name=name
                    if username:
                        if anchor.startswith('student:'):
                            profile=db.session.get(m['AuthProfile'],row.id) or auth['new_profile'](row);profile.username=username;profile.security_version+=1
                        else:
                            handle=db.session.get(Handle,anchor) or Handle(owner=anchor,username=username);handle.username=username;db.session.add(handle)
                else:abort(400)
                p.revision+=1;audit('member_profiles_'+action,anchor+'; person '+p.id);db.session.commit()
                if getattr(g,'profile_invite',None):app.extensions['staff_portal']['dispatch']([g.profile_invite])
                flash('Member profiles updated. Existing linked sessions must sign in again.');return redirect(url_for('admin_member_profiles',anchor=anchor))
            except IntegrityError:db.session.rollback();abort(409,'This username or profile type is already linked. Review existing accounts before retrying.')
        identities=[dict(key='admin:1',name=app.extensions['admin_security']['state']().name,email=app.extensions['admin_security']['state']().email)]
        identities.extend(dict(key='staff:'+str(row.id),name=row.name,email=row.email) for row in db.session.scalars(select(Staff).order_by(Staff.name)))
        identities.extend(dict(key='student:'+row.id,name=row.name,email=row.email) for row in db.session.scalars(select(Student).order_by(Student.name)))
        anchor=request.args.get('anchor','admin:1');row=account(anchor);p=person(anchor)
        return render_template('admin_member_profiles.html',identities=identities,anchor=anchor,record=row,person=p,members=members(p),profiles=available(p),instructor=extra(p,'instructor'),administrator=extra(p,'administrator'))
    def rename(identity,name):
        p=person(identity)
        if p:
            p.name=name;p.revision+=1
            for link in members(p):account(link.actor).name=name
    @app.after_request
    def retain_student_proof(response):
        context=session.get('linked_profiles')
        if context and session.get('auth_session'):context={**context,'student_token':session['auth_session']};session['linked_profiles']=context
        return response
    def audit_actor():return 'admin:'+g.linked_admin_actor if getattr(g,'linked_admin',None) else actor()
    def stamp_audit(session_):
        if getattr(g,'linked_admin',None):
            for row in session_.new:
                if isinstance(row,m['StaffAudit']) and row.actor=='admin:1':row.actor=audit_actor()
    def username(identity):
        if identity.startswith('student:'):
            profile=db.session.get(m['AuthProfile'],identity.split(':',1)[1]);return profile.username if profile else ''
        handle=db.session.get(Handle,identity)
        return handle.username if handle else 'admin' if identity=='admin:1' else account(identity).staff_number
    @app.context_processor
    def context():return dict(member_username=username,linked_person=getattr(g,'member_person',None),is_linked_admin=bool(getattr(g,'linked_admin',None)))
    app.extensions['member_profiles']=dict(guard=guard,contact_owner=contact_owner,confirmation_password=confirmation_password,person=person,extra=extra,available=available,actor=actor,rename=rename,stamp_audit=stamp_audit,audit_actor=audit_actor)
