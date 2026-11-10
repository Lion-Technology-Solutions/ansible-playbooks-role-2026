"""Verified activation, primary sign-in, server-enforced MFA and single-use recovery."""
import base64,hashlib,hmac,io,json,re,secrets,time,os,ipaddress
from datetime import timedelta
from urllib.parse import urlencode,quote,urlsplit
from flask import abort,flash,g,jsonify,redirect,render_template,request,session,url_for,Response
from sqlalchemy import select,text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash,generate_password_hash
import pyotp,qrcode
from webauthn import generate_registration_options,generate_authentication_options,verify_registration_response,verify_authentication_response,options_to_json
from webauthn.helpers.structs import AuthenticatorSelectionCriteria,UserVerificationRequirement,ResidentKeyRequirement,PublicKeyCredentialDescriptor
from authlib.integrations.requests_client import OAuth2Session
from authlib.jose import JsonWebToken
from authlib.oidc.core import CodeIDToken
from providers import ORIGIN,LEGACY_ORIGIN,api

RP_ID=urlsplit(ORIGIN).hostname
LEGACY_RP_ID=urlsplit(LEGACY_ORIGIN).hostname
DUMMY_PASSWORD=generate_password_hash('not-a-real-account-'+secrets.token_hex(20),method='pbkdf2:sha256:600000')
def b64(data):return base64.urlsafe_b64encode(data).decode().rstrip('=')
def unb64(data):return base64.urlsafe_b64decode(data+'='*(-len(data)%4))


def install(app,db,m,h):
    Policy,Profile,Login,Challenge,Factor,Recovery,Social,Mail=(m[k] for k in ('AuthPolicy','AuthProfile','AuthSession','AuthChallenge','AuthFactor','AuthRecoveryCode','AuthSocialIdentity','AuthMail'))
    Student,Identity,Attempt=m['Student'],m['StudentIdentity'],m['LoginAttempt']
    now,aware=m['now'],m['aware'];service=app.extensions['integrations']
    def client_address():
        value=request.headers.get('x-vercel-forwarded-for','') if os.environ.get('VERCEL')=='1' else ''
        try:return str(ipaddress.ip_address(value.strip()))
        except ValueError:return request.remote_addr or 'unknown'
    def digest(value):return hashlib.sha256(value.encode()).hexdigest()
    def mac(value):return hmac.new(app.secret_key.encode(),value.encode(),'sha256').hexdigest()
    def stamp(student):return mac(student.password_hash)
    def policy():return db.session.get(Policy,1)
    def audit(action,status='success'):db.session.add(m['IntegrationAudit'](provider='security',action=action,status=status))
    def transport(url,method='GET',payload=None,headers=None,form=False):return app.config.get('AUTH_HTTP',api)(url,method,payload,headers,form)
    def limit(key,count=10,minutes=15):
        key=mac('security-limit:'+key)
        if db.engine.dialect.name=='postgresql':db.session.execute(text('SELECT pg_advisory_xact_lock(73592032)'))
        row=db.session.scalar(select(Attempt).where(Attempt.key==key).with_for_update())
        if row and aware(row.last_at)<now()-timedelta(minutes=minutes):row.attempts=0
        if row and row.attempts>=count:db.session.rollback();abort(429,'Too many security attempts. Wait before trying again.')
        if not row:row=Attempt(key=key,attempts=0);db.session.add(row)
        row.attempts+=1;row.last_at=now();db.session.commit()
    def new_profile(student,username=''):
        username=username.strip().casefold() or 'student_'+student.id[:12]
        if not re.fullmatch(r'[a-z0-9_][a-z0-9_.-]{2,39}',username) or re.match(r'lt-\d',username):raise ValueError('Choose a username of 3–40 letters, digits, dots, underscores or hyphens; student numbers are reserved.')
        row=Profile(student_id=student.id,username=username,activated_at=None if policy().require_email else now())
        db.session.add(row);return row
    def profile(student):
        row=db.session.get(Profile,student.id)
        if not row:
            row=new_profile(student);row.activated_at=now();row.email_grace_until=now()+timedelta(days=policy().grace_days);db.session.commit()
        if not row.activated_at and (row.email_verified_at or not policy().require_email):row.activated_at=now();db.session.commit()
        return row
    def factors(sid):return db.session.scalars(select(Factor).where(Factor.student_id==sid).order_by(Factor.created_at)).all()
    def valid_login(token):
        row=db.session.get(Login,digest(token)) if token else None
        if not row or aware(row.expires_at)<now():return None
        student=db.session.get(Student,row.student_id);p=profile(student)
        return row if student and row.stamp==stamp(student) and row.version==p.security_version else None
    def pending():return valid_login(session.get('pending_auth',''))
    def current():return valid_login(session.get('auth_session',''))
    def due(student):
        p=profile(student)
        return policy().require_mfa and p.activated_at and aware(p.activated_at)+timedelta(days=policy().grace_days)<=now()
    def needs_email(student):
        p=profile(student)
        return policy().require_email and not p.email_verified_at and (not p.email_grace_until or aware(p.email_grace_until)<=now())
    def complete(row,token):
        student=db.get_or_404(Student,row.student_id)
        row.expires_at=now()+timedelta(hours=4)
        session.clear();session.update(student_id=student.id,student_auth=stamp(student),auth_session=token);session.permanent=True
        enrollment=db.session.get(m['Enrollment'],row.next_enrollment_id) if row.next_enrollment_id else None
        destination=url_for('global_payment',enrollment_id=enrollment.id) if enrollment and enrollment.student_id==student.id else url_for('portal')
        row.next_enrollment_id=None;audit('sign_in');db.session.commit();return redirect(destination)
    def advance(row,token):
        student=db.get_or_404(Student,row.student_id)
        if needs_email(student):return redirect(url_for('verify_email'))
        if factors(student.id) and not row.mfa_at:return redirect(url_for('mfa_challenge'))
        if due(student) and not factors(student.id):return redirect(url_for('security_account'))
        return complete(row,token)
    def begin(student,send_verification=False,next_enrollment=None):
        p=profile(student);token=secrets.token_urlsafe(32)
        row=Login(digest=digest(token),student_id=student.id,stamp=stamp(student),version=p.security_version,expires_at=now()+timedelta(minutes=15),next_enrollment_id=next_enrollment)
        db.session.add(row);db.session.commit();session.clear();session['pending_auth']=token;session['csrf']=secrets.token_hex(24)
        if send_verification and needs_email(student):
            try:send_verification_email(student,row)
            except Exception:flash('Your account was saved, but the verification email could not be queued. Try Resend code or contact support.')
        return advance(row,token)
    def actor(verified_email=True):
        row=current() if g.student else pending()
        if not row:abort(403,'Sign in again to manage account security.')
        student=db.get_or_404(Student,row.student_id)
        if verified_email and needs_email(student):abort(403,'Verify your email before setting up account security.')
        return student,row
    def fresh(row):return row.mfa_at and aware(row.mfa_at)>now()-timedelta(minutes=5)
    def manage_allowed(student,row):
        if factors(student.id) and not fresh(row):raise ValueError('Verify an existing security method first, then retry within five minutes.')
        if aware(row.created_at)<now()-timedelta(minutes=15):raise ValueError('Sign out and sign in again before changing security methods.')
    def challenge(student,purpose,binding,values,minutes=10,raw=None):
        raw=raw or secrets.token_urlsafe(32)
        row=Challenge(id=secrets.token_hex(16),student_id=student.id if student else None,purpose=purpose,digest=digest(raw),binding=binding,ciphertext='',stamp=stamp(student) if student else '',expires_at=now()+timedelta(minutes=minutes))
        row.ciphertext=service['seal']('auth_challenge',row.id,values);db.session.add(row);db.session.commit();return row,raw
    def load_challenge(identity,purpose,student=None,binding=None):
        row=db.session.scalar(select(Challenge).where(Challenge.id==identity).with_for_update().execution_options(populate_existing=True))
        if not row or row.purpose!=purpose or row.consumed or row.attempts>=5 or aware(row.expires_at)<now() or (student and (row.student_id!=student.id or row.stamp!=stamp(student))) or (binding is not None and row.binding!=binding):raise ValueError('The security challenge expired or was already used. Start a new one.')
        return row,service['unseal']('auth_challenge',row.id,row.ciphertext)
    def screen_challenge(identity,purpose,student,binding):
        try:return load_challenge(identity,purpose,student,binding)
        except ValueError:abort(400,'This setup expired or was already used. Return to Account security and start again.')
    def failed_challenge(row):row.attempts+=1;db.session.commit()
    def queue_mail(student,subject,body):
        row=Mail(id=secrets.token_hex(16),ciphertext='');row.ciphertext=service['seal']('auth_mail',row.id,dict(to=student.email,subject=subject,body=body))
        db.session.add(row);db.session.commit()
        try:
            publisher=app.config.get('SECURITY_MAIL_PUBLISH')
            if publisher:publisher(row.id)
            else:
                from integration_jobs import publish
                publish({'kind':'security_email','id':row.id,'run':secrets.token_hex(12)})
        except Exception:
            row.status='queue_failed';db.session.commit()
        return row
    def process_mail(identity):
        row=db.session.scalar(select(Mail).where(Mail.id==identity).with_for_update())
        if not row or row.status=='sent':return
        if aware(row.created_at)<now()-timedelta(minutes=30):row.status='expired';row.ciphertext='';db.session.commit();return
        if row.attempts>=5:return
        row.attempts+=1;db.session.commit()
        try:
            if not service['ready']('email'):raise ValueError()
            values=service['config']('email');data=service['unseal']('auth_mail',row.id,row.ciphertext)
            transport('https://api.sendgrid.com/v3/mail/send','POST',{'personalizations':[{'to':[{'email':data['to']}]}],'from':{'email':values['SENDGRID_FROM_EMAIL']},'subject':data['subject'],'content':[{'type':'text/plain','value':data['body']}]},{'Authorization':'Bearer '+values['SENDGRID_API_KEY']})
            row.status='sent';row.ciphertext='';db.session.commit()
        except Exception:row.status='failed';db.session.commit();raise RuntimeError('Security email delivery unavailable.') from None
    def send_verification_email(student,row):
        limit('email-send:'+student.id,3,15)
        code=f'{secrets.randbelow(1000000):06d}'
        old=db.session.scalars(select(Challenge).where(Challenge.student_id==student.id,Challenge.purpose=='email',Challenge.consumed.is_(False))).all()
        for item in old:item.consumed=True
        record,_=challenge(student,'email',row.digest,dict(code=mac('email:'+student.id+':'+code)),10)
        session['email_challenge']=record.id
        queue_mail(student,'Verify your LionTech Academy email',f'Hello {student.name},\nYour verification code is {code}. It expires in 10 minutes.\nStudent number: {student.student_number}\nIf you did not request this, ignore this message.')
    def recovery_codes(student):
        db.session.query(Recovery).filter_by(student_id=student.id).delete(synchronize_session=False)
        codes=[secrets.token_hex(8).upper() for _ in range(10)]
        for code in codes:db.session.add(Recovery(digest=mac('recovery:'+student.id+':'+code),student_id=student.id))
        return ['-'.join(code[i:i+4] for i in range(0,16,4)) for code in codes]
    def finish_factor(student,row):
        row.mfa_at=now();codes=[]
        if not db.session.scalar(select(Recovery.digest).where(Recovery.student_id==student.id)):codes=recovery_codes(student)
        row.expires_at=now()+timedelta(hours=4);audit('mfa_enrolled');db.session.commit()
        if pending():complete(row,session['pending_auth'])
        return render_template('security_codes.html',codes=codes)
    def password_login():
        if request.method=='POST':
            identifier=(request.form.get('identifier') or request.form.get('email') or '').strip().casefold()[:254]
            limit('password-ip:'+client_address(),30);limit('password:'+identifier,10)
            student=None;p=policy()
            if '@' in identifier and p.email_login:student=db.session.scalar(select(Student).where(Student.email==identifier))
            elif identifier.upper().startswith('LT-') and p.number_login:
                student=find_number(identifier)
            elif p.username_login:
                prof=db.session.scalar(select(Profile).where(Profile.username==identifier));student=db.session.get(Student,prof.student_id) if prof else None
            password=request.form.get('password','')
            valid=check_password_hash(student.password_hash if student else DUMMY_PASSWORD,password[:129])
            if student and valid and len(password)<=128:return begin(student)
            flash('Invalid sign-in details.')
        return render_template('login.html',admin=False)
    def find_number(number):
        match=re.fullmatch(r'LT-(\d{4})-(\d{6,})',number.strip().upper())
        if not match:return None
        identity=int(match[2])
        if not 1<=identity<=2147483647:return None
        ident=db.session.get(Identity,identity);student=db.session.get(Student,ident.student_id) if ident else None
        return student if student and student.student_number==number.strip().upper() else None

    @app.before_request
    def security_guard():
        if request.endpoint in ('static','health','logout') or not g.student or session.get('admin'):return
        student=g.student;p=profile(student)
        row=current()
        if session.get('auth_session') and not row:
            session.clear();return redirect(url_for('login'))
        if not row:
            # Upgrade legacy signed sessions without granting any MFA proof.
            return begin(student)
        if needs_email(student):
            token=session.pop('auth_session');session.pop('student_id',None);session.pop('student_auth',None);session['pending_auth']=token
            return redirect(url_for('verify_email'))
        enrolled=bool(factors(student.id))
        if (enrolled and not row.mfa_at) or (due(student) and not enrolled):
            token=session.pop('auth_session');session.pop('student_id',None);session.pop('student_auth',None);session['pending_auth']=token
            return redirect(url_for('mfa_challenge' if enrolled else 'security_account'))
    @app.context_processor
    def security_context():
        p=policy();return dict(auth_policy=p,social_available=lambda kind:p and getattr(p,kind+'_login') and service['ready'](kind+'_signin'),security_profile=profile(g.student) if g.student else None)

    @app.route('/verify-email',methods=['GET','POST'])
    def verify_email():
        student,row=actor(False)
        if request.method=='POST':
            if request.form.get('action')=='resend':
                try:send_verification_email(student,row);flash('Verification email queued. Check your inbox and spam folder.')
                except Exception:flash('The verification email could not be queued. Check with support or retry later.')
                return redirect(url_for('verify_email'))
            limit('email-check:'+student.id,10)
            try:
                record,values=load_challenge(session.get('email_challenge'),'email',student,row.digest)
                if not hmac.compare_digest(values['code'],mac('email:'+student.id+':'+request.form.get('code','').strip())):failed_challenge(record);raise ValueError('Incorrect code. Check your email and try again.')
                record.consumed=True;p=profile(student);p.email_verified_at=now();p.activated_at=p.activated_at or now();audit('email_verified');db.session.commit()
                return advance(row,session.get('pending_auth') or session['auth_session'])
            except ValueError as error:db.session.rollback();flash(str(error))
        return render_template('verify_email.html',email=student.email,student_number=student.student_number)

    @app.route('/password/forgot',methods=['GET','POST'])
    def forgot_password():
        if request.method=='POST':
            limit('reset-ip:'+client_address(),10,60)
            number=request.form.get('student_number','').strip().upper()[:40];email=request.form.get('email','').strip().casefold()[:254]
            limit('reset:'+number+':'+email,3,60)
            if policy().reset_email:
                student=find_number(number)
                if student and student.email==email:
                    try:
                        record,token=challenge(student,'reset','',{},30)
                        queue_mail(student,'Reset your LionTech Academy password',f'Use this single-use link within 30 minutes:\n{ORIGIN}/password/reset?token={token}\nStudent number: {student.student_number}\nPassword resets keep your two-factor authentication enabled. If you did not request this, ignore this message.')
                    except Exception:db.session.rollback()
            return render_template('forgot_password.html',sent=True)
        return render_template('forgot_password.html',sent=False)
    @app.route('/password/reset',methods=['GET','POST'])
    def reset_password():
        if request.args.get('token'):
            session['reset_token']=request.args['token'][:200];return redirect(url_for('reset_password'))
        token=session.get('reset_token','')
        record=db.session.scalar(select(Challenge).where(Challenge.digest==digest(token),Challenge.purpose=='reset').with_for_update()) if token else None
        student=db.session.get(Student,record.student_id) if record else None
        valid=record and student and not record.consumed and aware(record.expires_at)>now() and record.stamp==stamp(student)
        if not valid:return render_template('reset_password.html',valid=False),400
        if request.method=='POST':
            limit('reset-save:'+record.id,5)
            # limit() commits; re-lock and recheck to prevent simultaneous token reuse.
            record=db.session.scalar(select(Challenge).where(Challenge.id==record.id).with_for_update().execution_options(populate_existing=True))
            db.session.refresh(student)
            if record.consumed or record.stamp!=stamp(student) or aware(record.expires_at)<=now():abort(400,'This recovery link has expired or was used.')
            password=request.form.get('password','')
            if not 10<=len(password)<=128 or password!=request.form.get('confirm_password'):flash('Use matching passwords of 10–128 characters.')
            else:
                student.password_hash=generate_password_hash(password,method='pbkdf2:sha256:600000');profile(student).security_version+=1;record.consumed=True
                db.session.query(Login).filter_by(student_id=student.id).delete(synchronize_session=False);audit('password_reset');db.session.commit();session.clear()
                flash('Password updated. Sign in and complete your existing two-factor verification.');return redirect(url_for('login'))
        return render_template('reset_password.html',valid=True)

    def sms(values,code=None):
        if not service['ready']('sms_verify'):raise ValueError('SMS verification is awaiting administrator setup. Choose an authenticator app or passkey.')
        keys=service['config']('sms_verify');basic=base64.b64encode((keys['VERIFY_ACCOUNT_SID']+':'+keys['VERIFY_AUTH_TOKEN']).encode()).decode()
        path='VerificationCheck' if code is not None else 'Verifications'
        payload={'To':values['phone'],'Code':code} if code is not None else {'To':values['phone'],'Channel':'sms'}
        result=transport('https://verify.twilio.com/v2/Services/'+quote(keys['VERIFY_SERVICE_SID'],safe='')+'/'+path,'POST',payload,{'Authorization':'Basic '+basic},True)
        if result.get('status')!=('approved' if code is not None else 'pending'):raise ValueError('The SMS code was not verified. Check the number/code or choose another security method.')
        return result
    def totp_matches(factor,code):
        values=service['unseal']('auth_factor',factor.id,factor.ciphertext);totp=pyotp.TOTP(values['secret']);step=int(now().timestamp()//30)
        for counter in range(step-1,step+2):
            if counter>factor.last_step and hmac.compare_digest(totp.at(counter*30),code):factor.last_step=counter;return True
        return False
    def new_factor(student,method,values,**kwargs):
        factor=Factor(id=secrets.token_hex(16),student_id=student.id,method=method,ciphertext='',**kwargs)
        factor.ciphertext=service['seal']('auth_factor',factor.id,values);db.session.add(factor);return factor

    @app.route('/account/security',methods=['GET','POST'])
    def security_account():
        if not g.student and not pending():return redirect(url_for('login'))
        student,row=actor();p=profile(student)
        codes=None
        if request.method=='POST':
            limit('security-manage:'+student.id,15)
            try:
                manage_allowed(student,row);action=request.form.get('action')
                if action=='username':
                    username=request.form.get('username','').strip().casefold()
                    if not re.fullmatch(r'[a-z0-9_][a-z0-9_.-]{2,39}',username) or re.match(r'lt-\d',username):raise ValueError('Choose a valid 3–40 character username. Student numbers are reserved.')
                    p.username=username;audit('username_changed');db.session.commit();flash('Username updated.')
                elif action=='totp_start':
                    secret=pyotp.random_base32();record,_=challenge(student,'totp_setup',row.digest,{'secret':secret},10);session['totp_setup']=record.id
                    return redirect(url_for('totp_setup'))
                elif action=='sms_start':
                    phone=request.form.get('phone','').strip()
                    if not re.fullmatch(r'\+[1-9]\d{6,14}',phone):raise ValueError('Enter a phone number with + and country code.')
                    limit('sms-send-ip:'+client_address(),10);limit('sms-send:'+student.id,3)
                    sms({'phone':phone});record,_=challenge(student,'sms_setup',row.digest,{'phone':phone},5);session['sms_setup']=record.id
                    return redirect(url_for('sms_setup'))
                elif action=='recovery':
                    if not fresh(row):raise ValueError('Verify an existing method before regenerating recovery codes.')
                    codes=recovery_codes(student);audit('recovery_regenerated');db.session.commit();return render_template('security_codes.html',codes=codes)
                elif action=='remove':
                    if not fresh(row):raise ValueError('Verify an existing method before removing it.')
                    item=db.get_or_404(Factor,request.form.get('factor_id'))
                    if item.student_id!=student.id:abort(403)
                    if policy().require_mfa and len(factors(student.id))<=1:raise ValueError('Add and verify another method before removing your last security method.')
                    db.session.delete(item);p.security_version+=1;row.version=p.security_version;audit('factor_removed');db.session.commit();flash('Security method removed. Other sessions must sign in again.')
                else:abort(400)
            except (ValueError,IntegrityError) as error:db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'That username is already in use.')
            except Exception:db.session.rollback();flash('The security provider could not complete this request. Retry or choose another method.')
        return render_template('security_account.html',student=student,profile=p,factors=factors(student.id),required=bool(due(student)),due_at=aware(p.activated_at)+timedelta(days=policy().grace_days) if p.activated_at else None,sms_ready=service['ready']('sms_verify'),fresh=bool(fresh(row)),social_links=db.session.scalars(select(Social).where(Social.student_id==student.id)).all())

    @app.route('/account/security/authenticator',methods=['GET','POST'])
    def totp_setup():
        student,row=actor();record,values=screen_challenge(session.get('totp_setup'),'totp_setup',student,row.digest)
        if request.method=='POST':
            limit('totp-setup:'+student.id,10)
            record,values=load_challenge(record.id,'totp_setup',student,row.digest)
            code=request.form.get('code','').strip();secret=values['secret']
            if re.fullmatch(r'\d{6}',code) and pyotp.TOTP(secret).verify(code,valid_window=1,for_time=now()):
                record.consumed=True;factor=new_factor(student,'totp',{'secret':secret},last_step=int(now().timestamp()//30))
                return finish_factor(student,row)
            failed_challenge(record);flash('Incorrect authenticator code. Check device time and try again.')
        return render_template('totp_setup.html',secret=values['secret'],qr_url=url_for('totp_qr'))
    @app.get('/account/security/authenticator/qr')
    def totp_qr():
        student,row=actor();record,values=screen_challenge(session.get('totp_setup'),'totp_setup',student,row.digest)
        uri=pyotp.TOTP(values['secret']).provisioning_uri(name=student.email,issuer_name='LionTech Academy')
        data=io.BytesIO();qrcode.make(uri).save(data,format='PNG');return Response(data.getvalue(),mimetype='image/png')
    @app.route('/account/security/sms',methods=['GET','POST'])
    def sms_setup():
        student,row=actor();record,values=screen_challenge(session.get('sms_setup'),'sms_setup',student,row.digest)
        if request.method=='POST':
            limit('sms-check:'+student.id,10)
            try:
                record,values=load_challenge(record.id,'sms_setup',student,row.digest)
                code=request.form.get('code','').strip()
                if not re.fullmatch(r'\d{4,10}',code):raise ValueError('Enter the code from your text message.')
                sms(values,code);record.consumed=True;new_factor(student,'sms',{'phone':values['phone']});return finish_factor(student,row)
            except Exception:failed_challenge(record);flash('The SMS code could not be verified. Try again or restart setup.')
        return render_template('sms_setup.html',phone='••••'+values['phone'][-4:])

    @app.route('/account/security/challenge',methods=['GET','POST'])
    def mfa_challenge():
        student,row=actor()
        if not factors(student.id):return redirect(url_for('security_account'))
        if request.method=='POST':
            limit('mfa:'+student.id,10)
            try:
                action=request.form.get('action');verified=False;code=request.form.get('code','').strip()
                if action=='recovery':
                    code=re.sub(r'[-\s]','',code).upper()
                    recovery=db.session.scalar(select(Recovery).where(Recovery.student_id==student.id,Recovery.digest==mac('recovery:'+student.id+':'+code)).with_for_update())
                    if recovery and not recovery.used_at:recovery.used_at=now();row.recovery_used=True;verified=True
                else:
                    factor=db.session.scalar(select(Factor).where(Factor.id==request.form.get('factor_id'),Factor.student_id==student.id).with_for_update())
                    if not factor:raise ValueError('Choose a registered method.')
                    if action=='sms_send' and factor.method=='sms':
                        limit('sms-send-ip:'+client_address(),10);limit('sms-send:'+student.id,3);values=service['unseal']('auth_factor',factor.id,factor.ciphertext);sms(values)
                        record,_=challenge(student,'sms_login',row.digest,dict(factor_id=factor.id,phone=values['phone']),5);session['sms_login']=record.id
                        flash('SMS code sent. Enter it below.');return redirect(url_for('mfa_challenge'))
                    if factor.method=='totp' and re.fullmatch(r'\d{6}',code):verified=totp_matches(factor,code)
                    if factor.method=='sms' and re.fullmatch(r'\d{4,10}',code):
                        record,values=load_challenge(session.get('sms_login'),'sms_login',student,row.digest)
                        if values['factor_id']!=factor.id:raise ValueError('Restart SMS verification.')
                        sms(values,code);record.consumed=True;verified=True
                if not verified:raise ValueError('The security code was not accepted. Try again.')
                row.mfa_at=now();row.expires_at=now()+timedelta(hours=4);audit('mfa_verified');db.session.commit()
                if pending():return advance(row,session['pending_auth'])
                flash('Security method verified for the next five minutes.');return redirect(url_for('security_account'))
            except ValueError as error:db.session.rollback();flash(str(error))
            except Exception:db.session.rollback();flash('Verification could not be completed. Try another registered method or a recovery code.')
        return render_template('mfa_challenge.html',factors=factors(student.id),student_number=student.student_number)

    def passkey_site():
        host=request.host.split(':',1)[0].lower()
        if host==RP_ID or (app.testing and host=='localhost'):return RP_ID,ORIGIN
        if host==LEGACY_RP_ID:return LEGACY_RP_ID,LEGACY_ORIGIN
        abort(400,'Passkeys are available only on the academy production domain or the original portal address.')
    def passkeys_for_site(student_id,rp_id):
        return [f for f in factors(student_id) if f.method=='passkey' and service['unseal']('auth_factor',f.id,f.ciphertext).get('rp_id',LEGACY_RP_ID)==rp_id]

    @app.post('/account/security/passkey/options')
    def passkey_options():
        student,row=actor();limit('passkey-options:'+student.id,15)
        mode=request.form.get('mode');rp_id,origin=passkey_site()
        if mode=='register':
            try:manage_allowed(student,row)
            except ValueError as error:abort(403,str(error))
            options=generate_registration_options(rp_id=rp_id,rp_name='LionTech Academy',user_id=student.id.encode(),user_name=student.student_number,user_display_name=student.name,authenticator_selection=AuthenticatorSelectionCriteria(user_verification=UserVerificationRequirement.REQUIRED,resident_key=ResidentKeyRequirement.PREFERRED),exclude_credentials=[PublicKeyCredentialDescriptor(id=unb64(f.credential_id)) for f in passkeys_for_site(student.id,rp_id)])
        elif mode=='authenticate':
            keys=[PublicKeyCredentialDescriptor(id=unb64(f.credential_id)) for f in passkeys_for_site(student.id,rp_id)]
            if not keys:abort(400,'No passkey is registered for this hostname. Use a recovery code or another method; original-address passkeys remain usable on the original portal address.')
            options=generate_authentication_options(rp_id=rp_id,allow_credentials=keys,user_verification=UserVerificationRequirement.REQUIRED)
        else:abort(400)
        record,_=challenge(student,'passkey_'+mode,row.digest,{'challenge':b64(options.challenge),'rp_id':rp_id,'origin':origin},5);session['passkey_'+mode]=record.id
        return jsonify(json.loads(options_to_json(options)))
    @app.post('/account/security/passkey/verify')
    def passkey_verify():
        student,row=actor();limit('passkey-check:'+student.id,10)
        data=request.get_json(silent=True) or {};mode=data.get('mode')
        if mode not in ('register','authenticate'):abort(400)
        try:
            record,values=load_challenge(session.get('passkey_'+mode),'passkey_'+mode,student,row.digest);credential=data.get('credential')
            rp_id,origin=passkey_site()
            if values.get('rp_id')!=rp_id or values.get('origin')!=origin:raise ValueError()
            if mode=='register':
                manage_allowed(student,row)
                result=verify_registration_response(credential=credential,expected_challenge=unb64(values['challenge']),expected_rp_id=rp_id,expected_origin=origin,require_user_verification=True)
                new_factor(student,'passkey',{'public_key':b64(result.credential_public_key),'rp_id':rp_id,'origin':origin},credential_id=b64(result.credential_id),sign_count=result.sign_count);record.consumed=True
                # Codes are shown once on a server-rendered page; never put them in cookies.
                row.mfa_at=now();row.expires_at=now()+timedelta(hours=4)
                db.session.commit();audit('passkey_enrolled');db.session.commit()
            else:
                factor=db.session.scalar(select(Factor).where(Factor.student_id==student.id,Factor.credential_id==credential.get('id'),Factor.method=='passkey').with_for_update())
                if not factor:raise ValueError()
                stored=service['unseal']('auth_factor',factor.id,factor.ciphertext)
                if stored.get('rp_id',LEGACY_RP_ID)!=rp_id:raise ValueError()
                result=verify_authentication_response(credential=credential,expected_challenge=unb64(values['challenge']),expected_rp_id=rp_id,expected_origin=origin,credential_public_key=unb64(stored['public_key']),credential_current_sign_count=factor.sign_count,require_user_verification=True)
                user_handle=(credential.get('response') or {}).get('userHandle')
                if user_handle and unb64(user_handle)!=student.id.encode():raise ValueError()
                factor.sign_count=result.new_sign_count;record.consumed=True;row.mfa_at=now();row.expires_at=now()+timedelta(hours=4);audit('passkey_verified');db.session.commit()
            if pending():complete(row,session['pending_auth'])
            return jsonify({'redirect':url_for('security_account'),'recovery_setup':mode=='register'})
        except Exception:
            db.session.rollback();return jsonify({'error':'Passkey verification failed. Check the device, origin and challenge, then start again.'}),400

    def social_keys(provider):
        key=provider+'_signin';values=service['config'](key)
        prefix='GOOGLE_SIGNIN' if provider=='google' else 'FACEBOOK_SIGNIN'
        return values.get(prefix+'_CLIENT_ID',''),values.get(prefix+'_CLIENT_SECRET','')
    def social_exchange(provider,code,values):
        client_id,client_secret=social_keys(provider);redirect_uri=ORIGIN+'/auth/'+provider+'/callback'
        client=OAuth2Session(client_id,client_secret,redirect_uri=redirect_uri,token_endpoint_auth_method='client_secret_post',code_challenge_method='S256',default_timeout=15)
        token=client.fetch_token('https://oauth2.googleapis.com/token' if provider=='google' else 'https://graph.facebook.com/v23.0/oauth/access_token',code=code,code_verifier=values['verifier'])
        access=token['access_token']
        if provider=='google':
            jwks=transport('https://www.googleapis.com/oauth2/v3/certs')
            claims=JsonWebToken(['RS256']).decode(token['id_token'],jwks,claims_cls=CodeIDToken,claims_options={'iss':{'essential':True,'values':['https://accounts.google.com','accounts.google.com']},'aud':{'essential':True,'value':client_id},'sub':{'essential':True},'exp':{'essential':True}},claims_params={'nonce':values['nonce'],'client_id':client_id,'access_token':access})
            claims.validate(now=int(now().timestamp()),leeway=30)
            email=str(claims.get('email','')).lower();authoritative=email.endswith('@gmail.com') or bool(claims.get('hd'))
            return dict(subject=claims['sub'],email=email,name=claims.get('name',''),verified=claims.get('email_verified') is True and authoritative)
        app_token=client_id+'|'+client_secret
        debug=transport('https://graph.facebook.com/v23.0/debug_token?'+urlencode({'input_token':access}),headers={'Authorization':'Bearer '+app_token}).get('data',{})
        if not debug.get('is_valid') or str(debug.get('app_id'))!=client_id or not debug.get('user_id') or int(debug.get('expires_at',0))<=int(now().timestamp()):raise ValueError('Facebook token validation failed.')
        proof=hmac.new(client_secret.encode(),access.encode(),'sha256').hexdigest()
        identity=transport('https://graph.facebook.com/v23.0/me?'+urlencode({'fields':'id,name,email','appsecret_proof':proof}),headers={'Authorization':'Bearer '+access})
        if str(identity.get('id'))!=str(debug['user_id']):raise ValueError('Facebook account mismatch.')
        return dict(subject=str(identity['id']),email=identity.get('email','').lower(),name=identity.get('name',''),verified=False)

    @app.post('/auth/<provider>/start')
    def social_start(provider):
        if provider not in ('google','facebook'):abort(404)
        if not getattr(policy(),provider+'_login') or not service['ready'](provider+'_signin'):abort(400,'This social sign-in method has not been enabled and configured by the academy.')
        limit('social-start:'+client_address(),20)
        link=request.form.get('mode')=='link';sid='';binding=''
        if link:
            student,row=actor()
            try:manage_allowed(student,row)
            except ValueError as error:abort(403,str(error))
            sid=student.id;binding=row.digest
        state=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(24);verifier=secrets.token_urlsafe(48)
        setting=db.session.get(m['IntegrationSetting'],provider+'_signin');revision=setting.revision if setting else 0
        record,_=challenge(None,'oauth_'+provider,'',dict(nonce=nonce,verifier=verifier,link_sid=sid,login_binding=binding,revision=revision,application=session.get('application')),10,raw=state)
        session['social_state']=state
        client_id,_=social_keys(provider)
        client=OAuth2Session(client_id,scope='openid email profile' if provider=='google' else 'email public_profile',redirect_uri=ORIGIN+'/auth/'+provider+'/callback',code_challenge_method='S256')
        location,_=client.create_authorization_url('https://accounts.google.com/o/oauth2/v2/auth' if provider=='google' else 'https://www.facebook.com/v23.0/dialog/oauth',state=state,nonce=nonce,code_verifier=verifier)
        return redirect(location)
    @app.get('/auth/<provider>/callback')
    def social_callback(provider):
        if provider not in ('google','facebook'):abort(404)
        state=request.args.get('state','')
        if not state or not hmac.compare_digest(state,session.get('social_state','')):abort(403,'Invalid sign-in session. Start again.')
        record=db.session.scalar(select(Challenge).where(Challenge.digest==digest(state),Challenge.purpose=='oauth_'+provider).with_for_update())
        if not record or record.consumed or aware(record.expires_at)<now():abort(403,'This sign-in request expired or was used.')
        values=service['unseal']('auth_challenge',record.id,record.ciphertext);record.consumed=True;db.session.commit();session.pop('social_state',None)
        try:
            setting=db.session.get(m['IntegrationSetting'],provider+'_signin')
            if not getattr(policy(),provider+'_login') or not service['ready'](provider+'_signin') or values['revision']!=(setting.revision if setting else 0):raise ValueError()
            if request.args.get('error') or not request.args.get('code'):raise ValueError()
            info=app.config.get('SOCIAL_EXCHANGE',social_exchange)(provider,request.args['code'],values)
            if not isinstance(info.get('subject'),str) or not 1<=len(info['subject'])<=255:raise ValueError()
            linked=db.session.get(Social,(provider,info['subject']))
            if values['link_sid']:
                student,row=actor()
                if student.id!=values['link_sid'] or row.digest!=values['login_binding']:raise ValueError()
                manage_allowed(student,row)
                if linked and linked.student_id!=student.id:raise ValueError()
                if not linked:db.session.add(Social(provider=provider,subject=info['subject'],student_id=student.id))
                audit('social_linked');db.session.commit();flash('Social account linked. Portal two-factor requirements still apply.');return redirect(url_for('security_account'))
            if linked:return begin(db.get_or_404(Student,linked.student_id))
            # Existing accounts are never linked automatically using an email match.
            if info.get('email') and db.session.scalar(select(Student.id).where(Student.email==info['email'])):
                flash('An account may already use this email. Sign in with your existing portal credentials and link this provider under Account security.');return redirect(url_for('login'))
            signup,_=challenge(None,'social_signup','',dict(provider=provider,info=info,application=values.get('application')),10)
            session['social_signup']=signup.id;return redirect(url_for('social_complete'))
        except Exception:
            db.session.rollback();flash('Social sign-in could not be verified. Start again or use your portal credentials.');return redirect(url_for('login'))

    @app.route('/auth/complete',methods=['GET','POST'])
    def social_complete():
        try:record,values=load_challenge(session.get('social_signup'),'social_signup')
        except ValueError:return redirect(url_for('login'))
        info=values['info'];provider=values['provider']
        if request.method=='POST':
            limit('social-signup:'+client_address(),10)
            try:
                record,values=load_challenge(record.id,'social_signup');info=values['info']
                name=request.form.get('name','').strip();email=request.form.get('email','').strip().lower();country=request.form.get('country');phone=request.form.get('phone','').strip()
                if not 1<=len(name)<=100 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254 or country not in m['COUNTRIES'] or not 1<=len(phone)<=40:raise ValueError('Complete your name, email, residence and phone number.')
                if not getattr(policy(),provider+'_login') or not service['ready'](provider+'_signin'):raise ValueError('This sign-in method is no longer enabled.')
                student=Student(name=name,email=email,phone=phone,country=country,kind='new',approved=True,password_hash=generate_password_hash(secrets.token_urlsafe(48),method='pbkdf2:sha256:600000'))
                db.session.add(student);db.session.flush();p=new_profile(student,request.form.get('username',''))
                if info.get('verified') and info.get('email')==email:p.email_verified_at=now();p.activated_at=now()
                db.session.add(Identity(student_id=student.id));db.session.add(m['AccountRequest'](student_id=student.id));db.session.add(m['StudentPreference'](student_id=student.id,language=m['residence'](country)['language'],timezone=m['residence'](country)['timezone']))
                application=values.get('application');enrollment=None
                if application:
                    course=db.get_or_404(m['Course'],application.get('course_id'));cohort=db.get_or_404(m['Cohort'],application.get('cohort_id'))
                    if not course.publication.published or cohort.course_id!=course.id or cohort.start_date not in m['next_start_dates']():raise ValueError('Choose a current program and batch before creating your account.')
                    enrollment=m['Enrollment'](student_id=student.id,course_id=course.id,cohort_id=cohort.id);db.session.add(enrollment)
                db.session.add(Social(provider=provider,subject=info['subject'],student_id=student.id));record.consumed=True;db.session.commit()
                return begin(student,send_verification=True,next_enrollment=enrollment.id if enrollment else None)
            except (ValueError,IntegrityError) as error:db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'An account already uses those details. Sign in or contact support.')
        return render_template('social_complete.html',info=info,provider=provider)

    @app.route('/admin/security',methods=['GET','POST'])
    @h['admin_required']
    def security_settings():
        p=policy()
        if request.method=='POST':
            service['reauthenticate']()
            try:
                if request.form.get('revision')!=str(p.revision):raise ValueError('Settings changed in another session. Reload before saving.')
                values={key:request.form.get(key)=='yes' for key in ('username_login','number_login','email_login','google_login','facebook_login','require_email','reset_email','require_mfa')}
                if not any(values[key] for key in ('username_login','number_login','email_login')):raise ValueError('Keep at least one portal password sign-in method enabled for account recovery.')
                for kind in ('google','facebook'):
                    if values[kind+'_login'] and not service['ready'](kind+'_signin'):raise ValueError('Configure and test '+kind.title()+' Sign-in in Integrations before enabling it.')
                days=int(request.form.get('grace_days','7'))
                if days not in (0,7,14,30):raise ValueError('Choose an available grace period.')
                for key,value in values.items():setattr(p,key,value)
                p.grace_days=days;p.revision+=1;audit('policy_changed');db.session.commit();flash('Sign-in settings saved. The security policy applies to password and social sign-ins.')
            except ValueError as error:db.session.rollback();flash(str(error))
            return redirect(url_for('security_settings'))
        return render_template('security_settings.html',admin_view='security',policy=p,email_ready=service['ready']('email'),sms_ready=service['ready']('sms_verify'))

    @app.after_request
    def security_headers(response):
        if request.endpoint in ('reset_password','forgot_password','verify_email','totp_setup','totp_qr','social_callback','security_account','mfa_challenge','security_settings','passkey_verify'):
            response.headers['Referrer-Policy']='no-referrer';response.headers['Cache-Control']='no-store'
        return response
    return dict(registration_limit=lambda:limit('signup-ip:'+client_address(),20,60),new_profile=new_profile,begin=begin,password_login=password_login,process_mail=process_mail,policy=policy,profile=profile,due=due,social_exchange=social_exchange)
