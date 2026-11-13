"""Editable profiles, verified email changes and email-protected administrator recovery."""
import hashlib,hmac,io,os,re,secrets
from datetime import timedelta
from types import SimpleNamespace
from flask import abort,flash,g,redirect,render_template,request,Response,session,url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash,generate_password_hash
from PIL import Image,ImageOps
from werkzeug.exceptions import HTTPException
from file_storage import validate_image


def install(app,db,m,h):
    Admin,Picture,Student,Control=(m[n] for n in ('AdminAccount','ProfilePicture','Student','StudentControl'))
    now,aware=m['now'],m['aware'];auth=app.extensions['auth_security'];service=app.extensions['integrations']
    def state():
        row=db.session.get(Admin,1)
        if not row and db.engine.dialect.name=='postgresql':
            from sqlalchemy import text
            db.session.execute(text('SELECT pg_advisory_xact_lock(73592044)'));row=db.session.get(Admin,1,populate_existing=True)
        if not row:
            row=Admin(id=1,name='LionTech Administrator',email=app.config.get('ADMIN_EMAIL') or os.environ.get('SCHOOL_ADMIN_EMAIL') or 'liontechacademy@gmail.com');db.session.add(row);db.session.commit()
        return row
    def check(value):
        row=db.session.get(Admin,1)
        if not isinstance(value,str) or len(value)>128:return False
        return check_password_hash(row.password_hash,value) if row and row.password_hash else hmac.compare_digest(value.encode(),app.config['ADMIN_PASSWORD'].encode())
    def stamp():
        row=db.session.get(Admin,1);value=row.password_hash if row and row.password_hash else app.config['ADMIN_PASSWORD']
        return hmac.new(app.secret_key.encode(),value.encode(),'sha256').hexdigest()
    def mac(value):return hmac.new(app.secret_key.encode(),value.encode(),'sha256').hexdigest()
    def valid_email(value):return bool(len(value)<=254 and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value))
    def password():
        value=request.form.get('new_password','')
        if not 12<=len(value)<=128 or value!=request.form.get('confirm_password'):raise ValueError('Use matching administrator passwords of 12–128 characters.')
        return generate_password_hash(value,method='pbkdf2:sha256:600000')
    def avatar(owner):
        upload=request.files.get('picture')
        if not upload or not upload.filename:return
        data=upload.read(2*1024*1024+1)
        if not data or len(data)>2*1024*1024:raise ValueError('Choose a profile image up to 2 MB.')
        if upload.mimetype not in ('image/jpeg','image/png','image/webp'):raise ValueError('Choose a JPG, PNG or WebP profile image.')
        validate_image(data,upload.mimetype)
        with Image.open(io.BytesIO(data)) as image:
            image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((512,512));output=io.BytesIO();image.save(output,format='JPEG',quality=85)
        path=app.config['FILE_STORAGE'].put('portal-profiles/'+owner.replace(':','/')+'/'+secrets.token_hex(16)+'.jpg',output.getvalue())
        row=db.session.get(Picture,owner) or Picture(owner=owner)
        row.pathname=path;row.updated_at=now();db.session.add(row)
    def binding():
        session.setdefault('admin_flow_binding',secrets.token_hex(24));return mac(session['admin_flow_binding'])
    def send_admin_code(purpose,values):
        row=state()
        if values.get('eligible') is False:
            record,_=auth['challenge'](None,purpose,binding(),dict(eligible=False,code=mac(secrets.token_hex(32))),10)
            record.stamp=stamp();db.session.commit();session['admin_challenge']=record.id;session['admin_challenge_purpose']=purpose;return
        auth['limit']('admin-email:'+row.email,3,15)
        for old in db.session.scalars(select(m['AuthChallenge']).where(m['AuthChallenge'].purpose==purpose,m['AuthChallenge'].student_id.is_(None),m['AuthChallenge'].consumed.is_(False))):old.consumed=True
        record,_=auth['challenge'](None,purpose,binding(),values,10)
        code=f'{secrets.randbelow(1000000):06d}';values={**values,'code':mac('admin:'+record.id+':'+code)}
        record.stamp=stamp();record.ciphertext=service['seal']('auth_challenge',record.id,values);db.session.commit();session['admin_challenge']=record.id;session['admin_challenge_purpose']=purpose
        if values.get('eligible',True):
            auth['send_mail'](SimpleNamespace(email=row.email),'LionTech administrator verification',f'Your administrator verification code is {code}. It expires in 10 minutes.\nThis code authorizes a password change or account recovery. If you did not request this, do not share the code and contact LionTech support.',challenge_id=record.id)
    def send_profile_code(student,login,values):
        record,_=auth['challenge'](student,'profile_email_change',login.digest,values,10)
        code=f'{secrets.randbelow(1000000):06d}';values={**values,'code':mac('profile:'+record.id+':'+code)}
        record.ciphertext=service['seal']('auth_challenge',record.id,values);db.session.commit();session['profile_email_challenge']=record.id
        destination=values['old_email'] if values['stage']=='old' else values['new_email']
        auth['send_mail'](SimpleNamespace(email=destination),'Verify your LionTech email change',f'Your email-change verification code is {code}. It expires in 10 minutes.\nNever share this code. If you did not request an email change, contact support.',challenge_id=record.id)

    @app.context_processor
    def profile_context():
        return dict(admin_display_name=(g.member_person.name if getattr(g,'linked_admin',None) and getattr(g,'member_person',None) else db.session.get(Admin,1).name if session.get('admin') and db.session.get(Admin,1) else 'Administrator'),has_picture=lambda owner:bool(db.session.get(Picture,owner)),student_active=lambda student:not (db.session.get(Control,student.id) and not db.session.get(Control,student.id).active))

    @app.get('/account/picture/<kind>/<identity>')
    def profile_picture(kind,identity):
        if kind not in ('student','admin'):abort(404)
        owner=kind+':'+identity
        if not session.get('admin') and (kind!='student' or not g.student or g.student.id!=identity):abort(403)
        row=db.get_or_404(Picture,owner)
        return Response(app.config['FILE_STORAGE'].get(row.pathname),mimetype='image/jpeg',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @app.route('/account/profile',methods=['GET','POST'])
    @h['student_required']()
    def student_profile():
        student,login=auth['actor']()
        if request.method=='POST':
            try:
                name=request.form.get('name','').strip();email=request.form.get('email','').strip().casefold()
                if not 1<=len(name)<=100 or not valid_email(email):raise ValueError('Enter your name and a valid email address.')
                if email!=student.email:
                    auth['limit']('profile-email:'+student.id,3,15)
                    if db.session.scalar(select(Student.id).where(Student.email==email)):raise ValueError('This email cannot be used. Choose another address or contact support.')

                if name!=student.name:abort(403,'Only an administrator can change your name. Contact support to request a correction.')
                avatar('student:'+student.id);db.session.commit()
                if email!=student.email:
                    send_profile_code(student,login,dict(old_email=student.email,new_email=email,stage='old'))
                    flash('Confirm the code sent to your current email first. We will then verify your new address.');return redirect(url_for('profile_email_verify'))
                flash('Your profile has been updated.')
            except HTTPException:raise
            except (ValueError,IntegrityError) as error:db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'Those profile details could not be saved.')
            except Exception:db.session.rollback();flash('The profile update or verification email could not be completed. Please retry.')
        return render_template('student_profile.html',student=student)

    @app.route('/account/profile/verify-email',methods=['GET','POST'])
    @h['student_required']()
    def profile_email_verify():
        student,login=auth['actor']()
        if request.method=='POST':auth['limit']('profile-check:'+student.id,10)
        try:
            record,values=auth['load_challenge'](session.get('profile_email_challenge'),'profile_email_change',student,login.digest)
            if values['old_email']!=student.email:raise ValueError('The account changed. Start the email update again.')
            if request.method=='POST':
                if not hmac.compare_digest(values['code'],mac('profile:'+record.id+':'+request.form.get('code','').strip())):
                    record.attempts+=1;db.session.commit();raise ValueError('Incorrect verification code.')
                record.consumed=True
                if values['stage']=='old':
                    send_profile_code(student,login,dict(old_email=values['old_email'],new_email=values['new_email'],stage='new'))
                    flash('Current email verified. Enter the code sent to your new email.');return redirect(url_for('profile_email_verify'))
                old_email=student.email;student.email=values['new_email'];profile=auth['profile'](student);profile.email_verified_at=now();profile.security_version+=1
                db.session.query(m['AuthSession']).filter_by(student_id=student.id).delete(synchronize_session=False)
                app.extensions['notifications']['enqueue'](student,'profile_email_changed',payload={'recipient':old_email})
                db.session.commit();session.clear();flash('Email address verified and updated. Sign in again.');return redirect(url_for('login'))
        except (ValueError,IntegrityError) as error:
            db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'This email cannot be used. Start the update again.')
            if request.method=='GET':return redirect(url_for('student_profile'))
            values={'stage':'unknown'}
        except HTTPException:raise
        except Exception:
            db.session.rollback();flash('The verification email could not be sent. Your registered email has not changed. Start the update again or contact support.');return redirect(url_for('student_profile'))
        return render_template('profile_email_verify.html',stage=values['stage'])

    @app.route('/admin/profile',methods=['GET','POST'])
    @h['admin_required']
    def admin_profile():
        if getattr(g,'linked_admin',None):return redirect(url_for('staff_profile') if g.staff else url_for('member_contact'))
        row=state()
        if request.method=='POST':
            try:
                if request.form.get('action')=='change_password':
                    service['reauthenticate']();hashed=password();send_admin_code('admin_password_change',{'password_hash':hashed})
                    flash('Enter the code sent to your registered administrator email. Your password has not changed yet.');return redirect(url_for('admin_password_verify'))
                name=request.form.get('name','').strip()
                if not 1<=len(name)<=100:raise ValueError('Enter an administrator display name.')
                avatar('admin:1')
                if row.name!=name:app.extensions['member_profiles']['rename']('admin:1',name)
                row.name=name;db.session.commit();flash('Administrator profile updated.')
            except ValueError as error:db.session.rollback();flash(str(error))
            except HTTPException:raise
            except Exception:db.session.rollback();flash('The update or verification email could not be completed. Please retry.')
        return render_template('admin_profile.html',profile=row,admin_view='profile',email_ready=service['ready']('email'))

    @app.route('/admin/password/forgot',methods=['GET','POST'])
    def admin_forgot_password():
        sent=False
        if request.method=='POST':
            auth['limit']('admin-reset-ip:'+str(request.remote_addr),5,15)
            eligible=request.form.get('email','').strip().casefold()==state().email.casefold()
            try:send_admin_code('admin_password_reset',{'eligible':eligible})
            except Exception:app.logger.warning('Administrator recovery email unavailable.')
            sent=True
        return render_template('admin_forgot_password.html',sent=sent)

    @app.route('/admin/password/verify',methods=['GET','POST'])
    def admin_password_verify():
        if getattr(g,'linked_admin',None):abort(403,'Manage your own staff security credentials from your staff profile.')
        purpose=session.get('admin_challenge_purpose');reset=purpose=='admin_password_reset'
        if purpose not in ('admin_password_change','admin_password_reset'):return redirect(url_for('admin_forgot_password'))
        if not reset and not session.get('admin'):return redirect(url_for('admin_login'))
        if request.method=='POST':auth['limit']('admin-check:'+str(request.remote_addr),10,15)
        try:
            record,values=auth['load_challenge'](session.get('admin_challenge'),purpose,binding=binding())
            if not hmac.compare_digest(record.stamp,stamp()):raise ValueError('The account changed. Start again.')
            if request.method=='POST':
                if not values.get('eligible',True) or not hmac.compare_digest(values['code'],mac('admin:'+record.id+':'+request.form.get('code','').strip())):
                    record.attempts+=1;db.session.commit();raise ValueError('Incorrect or expired verification code.')
                hashed=password() if reset else values['password_hash'];row=state();row.password_hash=hashed;row.security_version+=1;record.consumed=True
                db.session.add(m['IntegrationAudit'](provider='security',action='admin_password_changed',status='email_verified'));db.session.commit()
                session.clear();flash('Administrator password updated. All previous admin sessions and portal API keys have been invalidated. Sign in with your new password.');return redirect(url_for('admin_login'))
        except ValueError as error:
            db.session.rollback();flash(str(error))
            if request.method=='GET':return redirect(url_for('admin_forgot_password' if reset else 'admin_profile'))
        return render_template('admin_password_verify.html',reset=reset)

    @app.after_request
    def profile_headers(response):
        if request.endpoint in ('student_profile','profile_email_verify','admin_profile','admin_forgot_password','admin_password_verify'):response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='no-referrer'
        return response
    return dict(check=check,stamp=stamp,state=state)
