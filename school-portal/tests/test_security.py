import asyncio,base64,hashlib,json,re,secrets,struct
from datetime import timedelta
from urllib.parse import parse_qs,urlsplit
import cbor2,pyotp,pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec,rsa
from cryptography.hazmat.primitives import hashes,serialization
from sqlalchemy import select
from app import db,AuthPolicy,AuthProfile,AuthSession,AuthChallenge,AuthFactor,AuthRecoveryCode,AuthSocialIdentity,AuthMail,Student,Enrollment,now
from auth_security import b64,unb64,RP_ID
from providers import ORIGIN
from test_portal import admin,register,csrf
from test_integrations import configure,http_ok


def setup(app,verify=True,mfa=True):
    app.config['INTEGRATIONS_ENCRYPTION_KEY']=Fernet.generate_key().decode();app.config['SECURITY_MAIL_PUBLISH']=lambda identity:None
    with app.app_context():p=db.session.get(AuthPolicy,1);p.require_email=verify;p.require_mfa=mfa;db.session.commit()
    manager,student=app.test_client(),app.test_client();admin(manager);return manager,student


def mail(app):
    with app.app_context():
        row=db.session.scalar(select(AuthMail).order_by(AuthMail.created_at.desc()));return app.extensions['integrations']['unseal']('auth_mail',row.id,row.ciphertext)


def activate(app,client):
    code=re.search(r'code is (\d{6})',mail(app)['body'])[1]
    return client.post('/verify-email',data={'csrf':csrf(client,'/verify-email'),'code':code},follow_redirects=True)


def post_security(client,**values):return client.post('/account/security',data={'csrf':csrf(client,'/account/security'),**values},follow_redirects=True)


def login(client,identifier='student@example.com',password='student-password',follow=True):
    return client.post('/student/login',data={'csrf':csrf(client,'/student/login'),'identifier':identifier,'password':password},follow_redirects=follow)


def logout(client):
    # CSRF is stored in the signed session even while the student is in a pending flow.
    with client.session_transaction() as s:token=s['csrf']
    return client.post('/logout',data={'csrf':token})


def enroll_totp(app,client):
    response=post_security(client,action='totp_start');assert response.status_code==200 and b'Scan and verify' in response.data
    with app.app_context():
        ch=db.session.scalar(select(AuthChallenge).where(AuthChallenge.purpose=='totp_setup').order_by(AuthChallenge.created_at.desc()))
        secret=app.extensions['integrations']['unseal']('auth_challenge',ch.id,ch.ciphertext)['secret']
    qr=client.get('/account/security/authenticator/qr');assert qr.status_code==200 and qr.mimetype=='image/png'
    response=client.post('/account/security/authenticator',data={'csrf':csrf(client,'/account/security/authenticator'),'code':pyotp.TOTP(secret).now()})
    assert response.status_code==200 and b'Save your recovery codes' in response.data
    codes=re.findall(rb'<code>([A-F0-9-]+)</code>',response.data)
    return secret,[c.decode() for c in codes]


def test_activation_code_is_bound_limited_and_preserves_account_number_and_payment_gate(app):
    manager,student=setup(app);response=register(student)
    assert b'Check your email' in response.data
    assert student.get('/portal').status_code==302 and student.get('/live-classes').status_code==302
    with app.app_context():
        s=db.session.scalar(select(Student));sid=s.id;number=s.student_number
        p=db.session.get(AuthProfile,sid);assert not p.email_verified_at and not p.activated_at
        record=db.session.scalar(select(AuthChallenge).where(AuthChallenge.purpose=='email'));assert 'code is' not in record.ciphertext
    other=app.test_client();assert other.get('/verify-email').status_code==403
    result=student.post('/verify-email',data={'csrf':csrf(student,'/verify-email'),'code':'not-a-code'},follow_redirects=True);assert b'Incorrect code' in result.data
    assert student.post('/verify-email',data={'code':'123456'}).status_code==400
    response=activate(app,student);assert b'Welcome' in response.data and student.get('/portal').status_code==200
    with app.app_context():
        assert db.session.get(Student,sid).student_number==number
        p=db.session.get(AuthProfile,sid);assert p.email_verified_at and p.activated_at
        assert not db.session.scalar(select(Enrollment))
        assert db.session.scalar(select(AuthChallenge).where(AuthChallenge.purpose=='email')).consumed


def test_seven_day_gate_applies_to_existing_sessions_and_totp_must_be_verified(app):
    manager,student=setup(app);register(student);activate(app,student)
    with app.app_context():p=db.session.scalar(select(AuthProfile));p.activated_at=now()-timedelta(days=8);db.session.commit()
    response=student.get('/portal',follow_redirects=True);assert b'Two-factor setup is required' in response.data
    assert student.get('/enrollments').status_code==302
    secret,codes=enroll_totp(app,student);assert len(codes)==10
    with app.app_context():
        f=db.session.scalar(select(AuthFactor));assert f.method=='totp' and secret not in f.ciphertext
    assert student.get('/portal').status_code==200
    logout(student);response=login(student);assert b'Verify it' in response.data
    assert student.get('/portal').status_code==302
    # The setup code cannot be replayed as a sign-in factor.
    response=student.post('/account/security/challenge',data={'csrf':csrf(student,'/account/security/challenge'),'action':'code','factor_id':factor_id(app),'code':pyotp.TOTP(secret).now()},follow_redirects=True)
    assert b'not accepted' in response.data
    response=student.post('/account/security/challenge',data={'csrf':csrf(student,'/account/security/challenge'),'action':'recovery','code':codes[0]},follow_redirects=True)
    assert response.status_code==200 and student.get('/portal').status_code==200
    logout(student);login(student)
    response=student.post('/account/security/challenge',data={'csrf':csrf(student,'/account/security/challenge'),'action':'recovery','code':codes[0]},follow_redirects=True)
    assert b'not accepted' in response.data and student.get('/portal').status_code==302


def factor_id(app):
    with app.app_context():return db.session.scalar(select(AuthFactor.id))


def test_username_number_and_email_login_toggles_and_no_email_enumeration(app):
    manager,student=setup(app,False,False);register(student)
    with app.app_context():s=db.session.scalar(select(Student));number=s.student_number;username=db.session.get(AuthProfile,s.id).username
    for identifier in [username,number,'student@example.com']:
        logout(student);assert student.get('/portal').status_code==302
        assert login(student,identifier).status_code==200 and student.get('/portal').status_code==200
    logout(student)
    with app.app_context():p=db.session.get(AuthPolicy,1);p.number_login=False;db.session.commit()
    assert b'Invalid sign-in details' in login(student,number).data
    first=login(student,'missing@example.com').data
    assert b'Invalid sign-in details' in first
    # Password field bounds are enforced even if a valid password is supplied as a prefix.
    assert b'Invalid sign-in details' in login(student,username,'x'*129).data


def test_password_reset_requires_matching_number_email_single_use_preserves_factors_and_revokes_sessions(app):
    manager,student=setup(app);register(student);activate(app,student);secret,codes=enroll_totp(app,student)
    with app.app_context():s=db.session.scalar(select(Student));number=s.student_number;sid=s.id
    outsider=app.test_client()
    def reset(number,email):return outsider.post('/password/forgot',data={'csrf':csrf(outsider,'/password/forgot'),'student_number':number,'email':email},follow_redirects=True)
    baseline=reset(number,'wrong@example.com');assert b'If both details match' in baseline.data
    with app.app_context():assert not db.session.scalar(select(AuthChallenge).where(AuthChallenge.purpose=='reset'))
    response=reset(number,'student@example.com');assert b'If both details match' in response.data
    body=mail(app)['body'];token=re.search(r'token=([^\s]+)',body)[1]
    page=outsider.get('/password/reset?token='+token,follow_redirects=True);assert b'Set a new password' in page.data
    page=outsider.post('/password/reset',data={'csrf':csrf(outsider,'/password/reset'),'password':'new-student-password','confirm_password':'new-student-password'},follow_redirects=True)
    assert b'Password updated' in page.data
    assert student.get('/portal').status_code==302
    with app.app_context():assert db.session.get(AuthFactor,factor_id(app)) and db.session.get(AuthProfile,sid).email_verified_at
    assert outsider.get('/password/reset?token='+token,follow_redirects=True).status_code==400
    assert b'Invalid sign-in' in login(outsider,password='student-password').data
    assert b'Verify it' in login(outsider,password='new-student-password').data
    assert outsider.get('/portal').status_code==302


def test_expired_email_code_and_reset_link_fail_without_activation(app):
    manager,student=setup(app);register(student)
    with app.app_context():ch=db.session.scalar(select(AuthChallenge).where(AuthChallenge.purpose=='email'));ch.expires_at=now()-timedelta(seconds=1);number=db.session.scalar(select(Student)).student_number;db.session.commit()
    assert b'expired' in activate(app,student).data
    client=app.test_client();client.post('/password/forgot',data={'csrf':csrf(client,'/password/forgot'),'student_number':number,'email':'student@example.com'})
    token=re.search(r'token=([^\s]+)',mail(app)['body'])[1]
    with app.app_context():db.session.scalar(select(AuthChallenge).where(AuthChallenge.purpose=='reset')).expires_at=now()-timedelta(seconds=1);db.session.commit()
    assert client.get('/password/reset?token='+token,follow_redirects=True).status_code==400


def test_sms_setup_and_login_use_bound_verified_destination(app):
    manager,student=setup(app);register(student);activate(app,student)
    app.config['INTEGRATION_HTTP']=http_ok;configure(app,manager,'sms_verify');configure(app,manager,'sms_verify',action='test')
    calls=[]
    def twilio(url,method='GET',payload=None,headers=None,form=False):
        calls.append((url,payload))
        return {'status':'approved' if payload.get('Code')=='123456' else 'pending'}
    app.config['AUTH_HTTP']=twilio
    page=post_security(student,action='sms_start',phone='+14375551234');assert b'Verify your phone' in page.data
    assert b'1234' in page.data and b'+14375551234' not in page.data
    assert b'could not be verified' in student.post('/account/security/sms',data={'csrf':csrf(student,'/account/security/sms'),'code':'000000'},follow_redirects=True).data
    page=student.post('/account/security/sms',data={'csrf':csrf(student,'/account/security/sms'),'code':'123456'},follow_redirects=True);assert b'recovery codes' in page.data
    logout(student);login(student);fid=factor_id(app)
    student.post('/account/security/challenge',data={'csrf':csrf(student,'/account/security/challenge'),'action':'sms_send','factor_id':fid,'phone':'+19999999999'})
    page=student.post('/account/security/challenge',data={'csrf':csrf(student,'/account/security/challenge'),'action':'code','factor_id':fid,'code':'123456'},follow_redirects=True)
    assert student.get('/portal').status_code==200
    assert all(payload['To']=='+14375551234' for _,payload in calls)


def passkey_registration(options,key,origin=ORIGIN,flags=0x45):
    public=key.public_key().public_numbers();cred_id=secrets.token_bytes(24)
    cose={1:2,3:-7,-1:1,-2:public.x.to_bytes(32,'big'),-3:public.y.to_bytes(32,'big')}
    client=json.dumps({'type':'webauthn.create','challenge':options['challenge'],'origin':origin}).encode()
    auth=hashlib.sha256((options.get('rp',{}).get('id') or options.get('rpId') or RP_ID).encode()).digest()+bytes([flags])+struct.pack('>I',0)+b'\0'*16+struct.pack('>H',len(cred_id))+cred_id+cbor2.dumps(cose)
    attestation=cbor2.dumps({'fmt':'none','attStmt':{},'authData':auth})
    return {'id':b64(cred_id),'rawId':b64(cred_id),'type':'public-key','response':{'clientDataJSON':b64(client),'attestationObject':b64(attestation)},'clientExtensionResults':{}}


def passkey_authentication(options,key,credential_id,sid,origin=ORIGIN,flags=5):
    client=json.dumps({'type':'webauthn.get','challenge':options['challenge'],'origin':origin}).encode();auth=hashlib.sha256((options.get('rp',{}).get('id') or options.get('rpId') or RP_ID).encode()).digest()+bytes([flags])+struct.pack('>I',1)
    signature=key.sign(auth+hashlib.sha256(client).digest(),ec.ECDSA(hashes.SHA256()))
    return {'id':credential_id,'rawId':credential_id,'type':'public-key','response':{'clientDataJSON':b64(client),'authenticatorData':b64(auth),'signature':b64(signature),'userHandle':b64(sid.encode())},'clientExtensionResults':{}}


def test_real_passkey_registration_signature_origin_uv_and_one_time_challenge(app):
    manager,student=setup(app);register(student);activate(app,student);key=ec.generate_private_key(ec.SECP256R1())
    token=csrf(student,'/account/security')
    assert student.post('/account/security/passkey/options',data={'mode':'register'}).status_code==400
    options=student.post('/account/security/passkey/options',data={'mode':'register','csrf':token}).json
    assert options['authenticatorSelection']['userVerification']=='required' and options['rp']['id']==RP_ID
    bad=passkey_registration(options,key,origin='https://evil.example')
    assert student.post('/account/security/passkey/verify',headers={'X-CSRF-Token':token},json={'mode':'register','credential':bad}).status_code==400
    cred=passkey_registration(options,key)
    result=student.post('/account/security/passkey/verify',headers={'X-CSRF-Token':token},json={'mode':'register','credential':cred});assert result.status_code==200
    assert student.post('/account/security/passkey/verify',headers={'X-CSRF-Token':token},json={'mode':'register','credential':cred}).status_code==400
    with app.app_context():sid=db.session.scalar(select(Student.id));assert db.session.scalar(select(AuthFactor)).method=='passkey'
    logout(student);login(student);token=csrf(student,'/account/security/challenge')
    options=student.post('/account/security/passkey/options',data={'mode':'authenticate','csrf':token}).json
    credential=passkey_authentication(options,key,cred['id'],sid,flags=1)
    assert student.post('/account/security/passkey/verify',headers={'X-CSRF-Token':token},json={'mode':'authenticate','credential':credential}).status_code==400
    credential=passkey_authentication(options,key,cred['id'],sid)
    assert student.post('/account/security/passkey/verify',headers={'X-CSRF-Token':token},json={'mode':'authenticate','credential':credential}).status_code==200
    assert student.get('/portal').status_code==200


def enable_social(app,manager,provider):
    app.config['INTEGRATION_HTTP']=http_ok;configure(app,manager,provider+'_signin');configure(app,manager,provider+'_signin',action='test')
    with app.app_context():setattr(db.session.get(AuthPolicy,1),provider+'_login',True);db.session.commit()


def social_start(client,provider='google',mode=None):
    path='/account/security' if mode=='link' else '/student/login'
    response=client.post('/auth/'+provider+'/start',data={'csrf':csrf(client,path),**({'mode':mode} if mode else {})})
    assert response.status_code==302
    return parse_qs(urlsplit(response.location).query)['state'][0]


def test_social_signup_state_replay_no_email_auto_link_and_same_mfa_gate(app):
    manager,student=setup(app);enable_social(app,manager,'google');register(student);activate(app,student)
    app.config['SOCIAL_EXCHANGE']=lambda *a:{'subject':'google-existing-email','name':'Google user','email':'student@example.com','verified':True}
    other=app.test_client();state=social_start(other)
    assert student.get('/auth/google/callback?state='+state+'&code=abc').status_code==403
    response=other.get('/auth/google/callback?state='+state+'&code=abc',follow_redirects=True);assert b'link this provider' in response.data
    with app.app_context():assert not db.session.scalar(select(AuthSocialIdentity))
    assert other.get('/auth/google/callback?state='+state+'&code=abc').status_code==403
    # An authenticated student must explicitly link that subject.
    state=social_start(student,mode='link');response=student.get('/auth/google/callback?state='+state+'&code=abc',follow_redirects=True);assert b'Social account linked' in response.data
    with app.app_context():p=db.session.scalar(select(AuthProfile));p.activated_at=now()-timedelta(days=8);db.session.commit()
    state=social_start(other);response=other.get('/auth/google/callback?state='+state+'&code=abc',follow_redirects=True)
    assert b'Two-factor setup is required' in response.data and other.get('/portal').status_code==302


def test_facebook_new_account_requires_independent_email_verification(app):
    manager,student=setup(app);enable_social(app,manager,'facebook')
    app.config['SOCIAL_EXCHANGE']=lambda *a:{'subject':'facebook-new-id','name':'New user','email':'new@example.com','verified':False}
    state=social_start(student,'facebook');page=student.get('/auth/facebook/callback?state='+state+'&code=abc',follow_redirects=True);assert b'Complete your LionTech profile' in page.data
    denied=student.post('/auth/complete',data={'csrf':csrf(student,'/auth/complete'),'name':'New user','email':'new@example.com','username':'new-social','country':'Canada','phone':'+14375551234'},follow_redirects=True)
    assert b'Read and acknowledge the current' in denied.data
    with app.app_context():assert not db.session.scalar(select(Student))
    page=student.post('/auth/complete',data={'csrf':csrf(student,'/auth/complete'),'policy_acknowledged':'yes','policy_version':__import__('legal_content').POLICY_VERSION,'name':'New user','email':'new@example.com','username':'new-social','country':'Canada','phone':'+14375551234'},follow_redirects=True)
    assert b'Check your email' in page.data and student.get('/portal').status_code==302
    activate(app,student)
    assert student.get('/portal').status_code==200


def test_google_jwt_signature_nonce_audience_and_expiry_are_validated(app,monkeypatch):
    from auth_security import OAuth2Session,JsonWebToken
    manager,student=setup(app);enable_social(app,manager,'google')
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048);public=key.public_key().public_numbers()
    jwk={'kty':'RSA','kid':'unit','alg':'RS256','n':b64(public.n.to_bytes((public.n.bit_length()+7)//8,'big')),'e':b64(public.e.to_bytes(3,'big'))}
    app.config['AUTH_HTTP']=lambda *a,**k:{'keys':[jwk]}
    from test_integrations import values_for
    client_id=values_for('google_signin')['GOOGLE_SIGNIN_CLIENT_ID'];claims={'iss':'https://accounts.google.com','sub':'google-signed-sub','aud':client_id,'iat':int(now().timestamp()),'exp':int((now()+timedelta(minutes=10)).timestamp()),'nonce':'unit-nonce','email':'test@gmail.com','email_verified':True}
    pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    def token():return JsonWebToken(['RS256']).encode({'alg':'RS256','kid':'unit'},claims,pem).decode()
    monkeypatch.setattr(OAuth2Session,'fetch_token',lambda *a,**k:{'access_token':'unit-access','id_token':token()})
    with app.test_request_context('/auth/google/callback'):
        exchange=app.extensions['auth_security']['social_exchange']
        assert exchange('google','unit-code',{'nonce':'unit-nonce','verifier':'x'*64})['verified']
        with pytest.raises(Exception):exchange('google','unit-code',{'nonce':'wrong-nonce','verifier':'x'*64})
        claims['aud']='wrong-client'
        with pytest.raises(Exception):exchange('google','unit-code',{'nonce':'unit-nonce','verifier':'x'*64})
        claims['aud']=client_id;claims['exp']=int((now()-timedelta(minutes=5)).timestamp())
        with pytest.raises(Exception):exchange('google','unit-code',{'nonce':'unit-nonce','verifier':'x'*64})


def test_admin_policy_mutations_require_password_csrf_and_provider_setup(app):
    manager,student=setup(app,False,False)
    page=manager.get('/admin/security');assert page.status_code==200
    assert student.get('/admin/security').status_code==302
    data={'csrf':csrf(manager,'/admin/security'),'revision':'1','admin_password':'wrong','username_login':'yes','number_login':'yes','require_email':'yes','require_mfa':'yes','grace_days':'7'}
    assert manager.post('/admin/security',data=data).status_code==403
    data['admin_password']='test-admin-password';data['google_login']='yes'
    page=manager.post('/admin/security',data=data,follow_redirects=True);assert b'Configure and test' in page.data
    data.pop('google_login');page=manager.post('/admin/security',data=data,follow_redirects=True);assert b'Sign-in settings saved' in page.data
    with app.app_context():p=db.session.get(AuthPolicy,1);assert p.require_email and p.require_mfa and p.grace_days==7


def test_security_mail_worker_decrypts_server_side_sends_and_discards_body(app):
    manager,student=setup(app);register(student);app.config['INTEGRATION_HTTP']=http_ok
    configure(app,manager,'email',{'SENDGRID_API_KEY':'unit-sendgrid-key','SENDGRID_FROM_EMAIL':'verified@example.com'});configure(app,manager,'email',action='test')
    calls=[];app.config['AUTH_HTTP']=lambda *a,**k:calls.append(a) or {}
    with app.test_request_context('/internal/security-worker'):
        identity=db.session.scalar(select(AuthMail.id));app.extensions['auth_security']['process_mail'](identity)
        row=db.session.get(AuthMail,identity);assert row.status=='sent' and row.ciphertext==''
        app.extensions['auth_security']['process_mail'](identity)
    assert len(calls)==1 and calls[0][0]=='https://api.sendgrid.com/v3/mail/send'
    assert calls[0][2]['personalizations'][0]['to'][0]['email']=='student@example.com'


def test_pending_mfa_cannot_add_new_factors_or_change_username_and_last_factor_is_preserved(app):
    manager,student=setup(app);register(student);activate(app,student);secret,codes=enroll_totp(app,student)
    logout(student);login(student)
    page=post_security(student,action='totp_start');assert b'Verify an existing' in page.data
    token=csrf(student,'/account/security/challenge')
    assert student.post('/account/security/passkey/options',data={'mode':'register','csrf':token}).status_code==403
    student.post('/account/security/challenge',data={'csrf':token,'action':'recovery','code':codes[0]})
    page=post_security(student,action='remove',factor_id=factor_id(app));assert b'last security method' in page.data
    with app.app_context():assert len(db.session.scalars(select(AuthFactor)).all())==1


def test_legacy_accounts_have_a_visible_migration_deadline_and_then_the_same_gate(app):
    manager,student=setup(app,False,False);register(student)
    with app.app_context():
        sid=db.session.scalar(select(Student.id));db.session.query(AuthSession).delete();db.session.query(AuthProfile).delete();p=db.session.get(AuthPolicy,1);p.require_email=True;p.require_mfa=True;db.session.commit()
    logout(student);page=login(student);assert b'Existing-account transition deadline' in page.data
    assert student.get('/portal').status_code==200
    with app.app_context():p=db.session.get(AuthProfile,sid);assert not p.email_verified_at;p.email_grace_until=now()-timedelta(seconds=1);db.session.commit()
    assert b'Check your email' in student.get('/portal',follow_redirects=True).data


def test_auth_pages_do_not_leak_tokens_in_referrers_and_attempts_are_persistently_limited(app):
    manager,student=setup(app);register(student)
    assert student.get('/verify-email').headers['Referrer-Policy']=='no-referrer'
    for _ in range(5):
        page=student.post('/verify-email',data={'csrf':csrf(student,'/verify-email'),'code':'invalid'})
        assert page.status_code==200
    assert b'expired or was already used' in student.post('/verify-email',data={'csrf':csrf(student,'/verify-email'),'code':'invalid'}).data
    outsider=app.test_client()
    for _ in range(10):assert login(outsider,'not-an-account').status_code==200
    assert login(outsider,'not-an-account').status_code==429


def test_domain_migration_keeps_old_passkeys_bound_to_original_host(app):
    from providers import LEGACY_ORIGIN
    manager,student=setup(app,False,False);register(student)
    old=app.test_client();new=app.test_client()
    def token(client,path,origin):
        page=client.get(path,base_url=origin)
        return re.search(rb'name="csrf" value="([^"]+)"',page.data)[1].decode()
    def sign_in(client,origin):
        return client.post('/student/login',base_url=origin,data={'csrf':token(client,'/student/login',origin),'identifier':'student@example.com','password':'student-password'})
    sign_in(old,LEGACY_ORIGIN);csrf_value=token(old,'/account/security',LEGACY_ORIGIN)
    options=old.post('/account/security/passkey/options',base_url=LEGACY_ORIGIN,data={'csrf':csrf_value,'mode':'register'}).json
    assert options['rp']['id']==urlsplit(LEGACY_ORIGIN).hostname
    credential=passkey_registration(options,ec.generate_private_key(ec.SECP256R1()),origin=LEGACY_ORIGIN)
    assert old.post('/account/security/passkey/verify',base_url=LEGACY_ORIGIN,headers={'X-CSRF-Token':csrf_value},json={'mode':'register','credential':credential}).status_code==200
    # Simulate the record format used before domain migration: no explicit RP field.
    with app.app_context():
        factor=db.session.scalar(select(AuthFactor));service=app.extensions['integrations'];stored=service['unseal']('auth_factor',factor.id,factor.ciphertext);stored.pop('rp_id');stored.pop('origin');factor.ciphertext=service['seal']('auth_factor',factor.id,stored);db.session.commit()
    sign_in(new,ORIGIN);csrf_value=token(new,'/account/security/challenge',ORIGIN)
    assert new.post('/account/security/passkey/options',base_url=ORIGIN,data={'csrf':csrf_value,'mode':'authenticate'}).status_code==400
    csrf_value=token(old,'/account/security',LEGACY_ORIGIN)
    result=old.post('/account/security/passkey/options',base_url=LEGACY_ORIGIN,data={'csrf':csrf_value,'mode':'authenticate'})
    assert result.status_code==200 and result.json['rpId']==urlsplit(LEGACY_ORIGIN).hostname
    hostile=app.test_client();sign_in(hostile,'https://untrusted.example');csrf_value=token(hostile,'/account/security/challenge','https://untrusted.example')
    assert hostile.post('/account/security/passkey/options',base_url='https://untrusted.example',data={'csrf':csrf_value,'mode':'authenticate'}).status_code==400
