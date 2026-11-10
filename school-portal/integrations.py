"""Admin integration configuration and per-student calendar authorization."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from flask import abort, current_app, flash, g, has_app_context, redirect, render_template, request, session, url_for
from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from integration_catalog import CATALOG, CATEGORIES, ENV_PROVIDER
from providers import ORIGIN, api


def credential(key,default=''):
    """Server-only lookup; database configuration overrides environment fallback."""
    if has_app_context() and 'integrations' in current_app.extensions:
        service=current_app.extensions['integrations']
        provider=ENV_PROVIDER.get(key)
        if provider:
            callbacks=request.endpoint=='stripe_webhook' if request else False
            return service['config'](provider,include_disabled=callbacks).get(key,default)
    return os.environ.get(key,default)


def install(app,db,m,h):
    Setting,Audit,Connection,State,Preference,Link=(m[key] for key in ('IntegrationSetting','IntegrationAudit','StudentConnection','CalendarOAuthState','StudentIntegrationPreference','CalendarEventLink'))
    now,aware=m['now'],m['aware'];admin_required,student_required=h['admin_required'],h['student_required']

    def cipher():
        key=app.config.get('INTEGRATIONS_ENCRYPTION_KEY') or os.environ.get('INTEGRATIONS_ENCRYPTION_KEY')
        if not key:raise ValueError('Integration encryption is not configured on this deployment.')
        keys=[key]+[value.strip() for value in os.environ.get('INTEGRATIONS_PREVIOUS_KEYS','').split(',') if value.strip()]
        try:return MultiFernet([Fernet(value.encode() if isinstance(value,str) else value) for value in keys])
        except ValueError:raise ValueError('Integration encryption requires deployment configuration.')

    def seal(provider,owner,values):
        return cipher().encrypt(json.dumps({'provider':provider,'owner':owner,'values':values},sort_keys=True).encode()).decode()

    def unseal(provider,owner,value):
        try:
            data=json.loads(cipher().decrypt(value.encode()))
            if data.get('provider')!=provider or data.get('owner')!=owner or not isinstance(data.get('values'),dict):raise ValueError()
            return data['values']
        except (InvalidToken,ValueError,TypeError,KeyError):raise ValueError('Saved credentials could not be read. Check the deployment encryption key.')

    def config(provider,include_disabled=False):
        row=db.session.get(Setting,provider)
        if row:
            if not row.enabled and not include_disabled:return {}
            try:return unseal(provider,'academy',row.ciphertext)
            except ValueError:return {}
        return {field['key']:os.environ.get(field['key'],'') for field in CATALOG[provider]['fields']}

    def generation(provider):
        values=config(provider)
        return hashlib.sha256((values.get('GOOGLE_CLIENT_ID','')+values.get('MICROSOFT_CLIENT_ID','')+values.get('MICROSOFT_TENANT','common')).encode()).hexdigest()

    def ready(provider):
        values=config(provider);row=db.session.get(Setting,provider)
        complete=all(values.get(field['key']) for field in CATALOG[provider]['fields'] if field['required'])
        return complete and (not row or row.status in ('connected','oauth_ready'))

    def audit(provider,action,status):db.session.add(Audit(provider=provider,action=action,status=status))

    def reauthenticate():
        if getattr(g,'portal_internal_delegation',False) and request.endpoint in ('admin','schedule_save'):return
        key=hashlib.sha256(('integration-reauth:'+session.get('admin_auth','')+str(request.remote_addr)).encode()).hexdigest()
        Attempt=m['LoginAttempt'];attempt=db.session.get(Attempt,key)
        if attempt and aware(attempt.last_at)<now()-timedelta(minutes=15):attempt.attempts=0
        if attempt and attempt.attempts>=10:abort(429,'Too many confirmation attempts. Try again in 15 minutes.')
        valid=app.extensions['admin_security']['check'](request.form.get('admin_password',''))
        if not valid:
            if not attempt:attempt=Attempt(key=key,attempts=0);db.session.add(attempt)
            attempt.attempts+=1;attempt.last_at=now();db.session.commit()
            abort(403,'Confirm your current administrator password to change integration settings.')
        if attempt:attempt.attempts=0

    def transport(url,method='GET',payload=None,headers=None,form=False):
        return app.config.get('INTEGRATION_HTTP',api)(url,method,payload,headers,form)

    def check(provider,values):
        bearer=lambda token:{'Authorization':'Bearer '+token}
        if provider in ('google_signin','facebook_signin'):
            return 'oauth_ready','Application settings saved. Register the exact sign-in callback and enable this provider in Sign-in settings. A real user must complete consent to verify the login flow.'
        if provider=='sms_verify':
            basic=base64.b64encode((values['VERIFY_ACCOUNT_SID']+':'+values['VERIFY_AUTH_TOKEN']).encode()).decode()
            result=transport('https://verify.twilio.com/v2/Services/'+quote(values['VERIFY_SERVICE_SID'],safe=''),headers={'Authorization':'Basic '+basic})
            if result.get('sid')!=values['VERIFY_SERVICE_SID']:raise ValueError('Twilio Verify service could not be verified.')
            return 'connected','Verify service authentication passed. Actual SMS delivery requires approved destinations, available billing and a student-initiated test.'
        if provider=='openai':
            model=values['OPENAI_MODEL']
            if not re.fullmatch(r'[a-zA-Z0-9._-]{1,100}',model):raise ValueError('Use a valid Responses API model identifier.')
            result=transport('https://api.openai.com/v1/models/'+quote(model,safe=''),headers=bearer(values['OPENAI_API_KEY']))
            if result.get('id')!=model:raise ValueError('The selected model could not be verified.')
            return 'connected','API key and model access verified. Responses generation also requires permission, available quota and billing. Open AI Assistant to try it.'
        if provider=='stripe':
            result=transport('https://api.stripe.com/v1/account',headers=bearer(values['STRIPE_SECRET_KEY']))
            if not str(result.get('id','')).startswith('acct_'):raise ValueError('Stripe did not verify the merchant account.')
            if not result.get('charges_enabled'):return 'attention','Account authenticated. Enable charges in Stripe before accepting payments.'
            return 'connected','Merchant authentication verified. Webhook signing still requires a real provider event test.'
        if provider=='zoom':
            basic=base64.b64encode((values['ZOOM_CLIENT_ID']+':'+values['ZOOM_CLIENT_SECRET']).encode()).decode()
            result=transport('https://zoom.us/oauth/token','POST',{'grant_type':'account_credentials','account_id':values['ZOOM_ACCOUNT_ID']},{'Authorization':'Basic '+basic},True)
            token=result.get('access_token')
            if not token:raise ValueError('Zoom did not issue an account token.')
            user=transport('https://api.zoom.us/v2/users/'+quote(values['ZOOM_HOST_USER'],safe=''),headers=bearer(token))
            if not user.get('id'):raise ValueError('Zoom host could not be verified.')
            return 'connected','Zoom account and host verified. Meeting creation/update requires the configured app scopes.'
        if provider=='email':
            result=transport('https://api.sendgrid.com/v3/scopes',headers=bearer(values['SENDGRID_API_KEY']))
            if 'mail.send' not in result.get('scopes',[]):raise ValueError('This SendGrid key needs Mail Send permission.')
            return 'connected','Mail Send permission verified. The From address must also be verified in SendGrid.'
        if provider in ('google','microsoft'):
            return 'oauth_ready','OAuth application settings saved. Each student must authorize calendar access before an account is connected.'
        if provider=='flutterwave':
            result=transport('https://api.flutterwave.com/v3/balances',headers=bearer(values['FLW_SECRET_KEY']))
            if result.get('status')!='success':raise ValueError('Flutterwave authentication was not verified.')
            return 'connected','V3 merchant API authentication verified. Register the V3 webhook URL and matching secret hash. Checkout is for enrollment fees; sandbox payments do not grant class access.'
        if provider=='whatsapp':
            basic=base64.b64encode((values['TWILIO_ACCOUNT_SID']+':'+values['TWILIO_AUTH_TOKEN']).encode()).decode()
            result=transport('https://api.twilio.com/2010-04-01/Accounts/'+quote(values['TWILIO_ACCOUNT_SID'],safe='')+'.json',headers={'Authorization':'Basic '+basic})
            if result.get('sid')!=values['TWILIO_ACCOUNT_SID'] or result.get('status')!='active':raise ValueError('An active Twilio account could not be verified.')
            return 'connected','Twilio account verified. Portal alerts require an approved Text-only template. Check Notifications for message delivery status.'
        if provider=='facebook':
            result=transport('https://graph.facebook.com/v23.0/oauth/access_token','POST',{'client_id':values['FACEBOOK_APP_ID'],'client_secret':values['FACEBOOK_APP_SECRET'],'grant_type':'client_credentials'},form=True)
            if not result.get('access_token'):raise ValueError('Meta did not verify the application.')
            if values.get('FACEBOOK_PAGE_ID') and values.get('FACEBOOK_PAGE_TOKEN'):
                page=transport('https://graph.facebook.com/v23.0/'+quote(values['FACEBOOK_PAGE_ID'],safe='')+'?fields=id',headers=bearer(values['FACEBOOK_PAGE_TOKEN']))
                if str(page.get('id'))!=values['FACEBOOK_PAGE_ID']:raise ValueError('Facebook Page access could not be verified.')
            return 'connected','Meta application authenticated. Social sign-in, publishing and lead ingestion are not enabled.'
        if provider=='youtube':
            result=transport('https://www.googleapis.com/youtube/v3/channels?'+urlencode({'part':'id','id':values['YOUTUBE_CHANNEL_ID'],'key':values['YOUTUBE_API_KEY']}))
            if not any(item.get('id')==values['YOUTUBE_CHANNEL_ID'] for item in result.get('items',[])):raise ValueError('The public YouTube channel could not be verified.')
            return 'connected','Public channel access verified. Upload and playlist automation are not enabled.'
        if provider=='instagram':
            result=transport('https://graph.facebook.com/v23.0/'+quote(values['INSTAGRAM_BUSINESS_ID'],safe='')+'?fields=id,username',headers=bearer(values['INSTAGRAM_ACCESS_TOKEN']))
            if str(result.get('id'))!=values['INSTAGRAM_BUSINESS_ID']:raise ValueError('Instagram professional account access could not be verified.')
            return 'connected','Professional account access verified. Publishing is not enabled.'
        if provider=='linkedin':
            if not values.get('LINKEDIN_ACCESS_TOKEN'):return 'oauth_ready','Application settings saved. Add an authorized profile token to validate account access; sign-in/sharing are not enabled.'
            result=transport('https://api.linkedin.com/v2/userinfo',headers=bearer(values['LINKEDIN_ACCESS_TOKEN']))
            if not result.get('sub'):raise ValueError('Authorized LinkedIn profile could not be verified.')
            return 'connected','Authorized profile verified. Sign-in and sharing are not enabled.'
        raise ValueError('Unsupported provider.')

    def status(provider):
        row=db.session.get(Setting,provider);values=config(provider,include_disabled=True)
        complete=all(values.get(field['key']) for field in CATALOG[provider]['fields'] if field['required'])
        if row and not row.enabled:return {'label':'Paused','kind':'paused','detail':'New actions are paused. Existing Stripe webhooks can still be verified.','checked_at':row.checked_at}
        if not complete:return {'label':'Not connected','kind':'disconnected','detail':'Complete the setup fields to configure this provider.','checked_at':None}
        state=row.status if row else 'needs_test'
        labels={'connected':'Connection verified','needs_test':'Saved · test required','oauth_ready':'OAuth setup saved · consent required','attention':'Action required','error':'Connection test failed'}
        return {'label':labels.get(state,'Saved · test required'),'kind':state,'detail':row.detail if row else 'Credentials are configured in the deployment. Save and validate them here to manage them from this hub.','checked_at':row.checked_at if row else None}

    @app.get('/admin/integrations')
    @admin_required
    def integration_hub():
        category=request.args.get('category','all');query=request.args.get('q','').strip()[:100]
        if category not in CATEGORIES:abort(400)
        entries=[(slug,entry,status(slug)) for slug,entry in CATALOG.items() if (category=='all' or entry['category']==category) and (not query or query.casefold() in (entry['name']+' '+entry['description']).casefold())]
        return render_template('integrations_hub.html',admin_view='integrations',entries=entries,categories=CATEGORIES,category=category,query=query,activity=db.session.scalars(select(Audit).order_by(Audit.id.desc()).limit(8)).all(),catalog=CATALOG)

    @app.get('/admin/integrations/activity')
    @admin_required
    def integration_activity():
        return render_template('integration_activity.html',admin_view='integrations',activity=db.session.scalars(select(Audit).order_by(Audit.id.desc()).limit(200)).all(),catalog=CATALOG)

    @app.route('/admin/integrations/<provider>',methods=['GET','POST'])
    @admin_required
    def integration_detail(provider):
        if provider not in CATALOG:abort(404)
        entry=CATALOG[provider];row=db.session.get(Setting,provider)
        if request.method=='POST':
            reauthenticate();action=request.form.get('action')
            try:
                if row and request.form.get('revision')!=str(row.revision):raise ValueError('Settings changed in another session. Reload before saving.')
                values=config(provider,include_disabled=True)
                if action=='save':
                    for item in entry['fields']:
                        key=item['key'];value=request.form.get(key,'').strip()
                        if len(value)>16000 or any(ord(char)<32 for char in value):raise ValueError('Check the provider field limits; paste single-line credential values.')
                        if request.form.get('clear_'+key)=='yes':values[key]=''
                        elif value:values[key]=value
                    if any(not values.get(item['key']) for item in entry['fields'] if item['required']):raise ValueError('Complete every required provider field. Blank saved fields keep their existing value.')
                    if provider=='openai' and not re.fullmatch(r'[a-zA-Z0-9._-]{1,100}',values['OPENAI_MODEL']):raise ValueError('Use a valid Responses API model identifier.')
                    if provider=='email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',values['SENDGRID_FROM_EMAIL']):raise ValueError('Enter the verified sender email address.')
                    if provider=='microsoft' and not re.fullmatch(r'(common|organizations|consumers|[a-fA-F0-9-]{36})',values['MICROSOFT_TENANT']):raise ValueError('Use common, organizations, consumers or a tenant GUID.')
                    if provider=='whatsapp' and not re.fullmatch(r'whatsapp:\+\d{7,15}',values['TWILIO_WHATSAPP_FROM']):raise ValueError('Use whatsapp:+ followed by the full international sender number.')
                    if provider=='flutterwave' and values.get('FLW_CHECKOUT_CURRENCY'):
                        from flutterwave_payments import CURRENCIES as FLW_CURRENCIES
                        values['FLW_CHECKOUT_CURRENCY']=values['FLW_CHECKOUT_CURRENCY'].upper()
                        if values['FLW_CHECKOUT_CURRENCY'] not in FLW_CURRENCIES:raise ValueError('Choose a supported Flutterwave currency, such as USD, NGN, GHS or XAF.')
                    encrypted=seal(provider,'academy',values)
                    if not row:row=Setting(provider=provider,ciphertext=encrypted);db.session.add(row)
                    else:row.ciphertext=encrypted;row.revision+=1
                    row.enabled=True;row.status='needs_test';row.detail='Saved securely. Test the connection before using this provider.';row.checked_at=None;row.updated_at=now()
                    audit(provider,'settings_saved','saved');db.session.commit();flash('Provider settings saved securely. Run Test connection to validate access.')
                elif action=='test':
                    if not row:raise ValueError('Save the integration settings before testing.')
                    if any(not values.get(item['key']) for item in entry['fields'] if item['required']):raise ValueError('Complete the required setup fields before testing.')
                    try:state,message=check(provider,values)
                    except HTTPError as error:state,message='error',f'The provider rejected the connection (HTTP {error.code}). Check credentials, permissions and account activation.'
                    except ValueError:state,message='error','The provider could not verify this setup. Check credentials, permissions and provider approval.'
                    except Exception:state,message='error','The provider could not be reached or returned an unexpected response. Retry later.'
                    row.status,row.detail,row.checked_at=state,message,now();audit(provider,'connection_test',state);db.session.commit();dispatch();flash(message)
                elif action=='test_generation' and provider=='openai':
                    if not row or not row.enabled:raise ValueError('Save and enable OpenAI before testing an AI answer.')
                    try:
                        message=app.extensions['assistant']['probe']()
                        row.status='connected';row.detail=message;row.checked_at=now();db.session.commit();flash(message)
                    except ValueError as error:
                        row.detail=str(error);db.session.commit();flash(str(error))
                elif action in ('pause','resume'):
                    if not row:raise ValueError('Save settings before changing connection state.')
                    row.enabled=action=='resume';row.revision+=1;row.updated_at=now();audit(provider,action,'paused' if action=='pause' else 'resumed');db.session.commit();dispatch();flash('Connection paused.' if action=='pause' else 'Connection resumed. Validate its status before use.')
                else:abort(400)
            except (ValueError,IntegrityError) as error:
                db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'The integration changed in another session. Reload and retry.')
            return redirect(url_for('integration_detail',provider=provider))
        values=config(provider,include_disabled=True)
        public={item['key']:values.get(item['key'],'') for item in entry['fields'] if not item['secret']}
        return render_template('integration_detail.html',admin_view='integrations',provider=provider,entry=entry,connection=status(provider),public=public,saved={item['key']:bool(values.get(item['key'])) for item in entry['fields']},revision=row.revision if row else 0,callback=ORIGIN+'/connections/'+provider+'/callback' if provider in ('google','microsoft') else None,webhook=ORIGIN+'/webhooks/'+provider if provider in ('stripe','flutterwave') else None)

    def calendar_definition(provider):
        values=config(provider)
        if provider=='google':
            return dict(client_id=values.get('GOOGLE_CLIENT_ID',''),client_secret=values.get('GOOGLE_CLIENT_SECRET',''),authorize='https://accounts.google.com/o/oauth2/v2/auth',token='https://oauth2.googleapis.com/token',scope='https://www.googleapis.com/auth/calendar.app.created')
        tenant=values.get('MICROSOFT_TENANT','common')
        if not re.fullmatch(r'(common|organizations|consumers|[a-fA-F0-9-]{36})',tenant):raise ValueError('Invalid Microsoft tenant configuration.')
        return dict(client_id=values.get('MICROSOFT_CLIENT_ID',''),client_secret=values.get('MICROSOFT_CLIENT_SECRET',''),authorize='https://login.microsoftonline.com/'+tenant+'/oauth2/v2.0/authorize',token='https://login.microsoftonline.com/'+tenant+'/oauth2/v2.0/token',scope='offline_access https://graph.microsoft.com/Calendars.ReadWrite')

    def connection_status(provider):
        row=db.session.get(Connection,(g.student.id,provider))
        if not ready(provider):return 'Academy setup required'
        if not row:return 'Not connected'
        if row.generation!=generation(provider):return 'Reconnect required'
        return 'Connected' if row.status=='connected' else 'Reconnect required'

    @app.route('/account/connections',methods=['GET','POST'])
    @student_required()
    def connected_accounts():
        if request.method=='POST':
            pref=db.session.get(Preference,g.student.id) or Preference(student_id=g.student.id)
            pref.reminders=request.form.get('reminders')=='yes';db.session.add(pref)
            alerts=db.session.get(m['ClassWhatsAppPreference'],g.student.id) or m['ClassWhatsAppPreference'](student_id=g.student.id,phone='')
            enabled=request.form.get('whatsapp')=='yes'
            phone=request.form.get('whatsapp_phone','').strip()
            if enabled and not re.fullmatch(r'\+[1-9][0-9]{6,14}',phone):
                db.session.rollback();flash('Enter your own WhatsApp number with country code, for example +14375550123.');return redirect(url_for('connected_accounts'))
            alerts.enabled=enabled
            if enabled:alerts.phone=phone;alerts.consented_at=now()
            payment=db.session.get(m['TuitionWhatsAppPreference'],g.student.id) or m['TuitionWhatsAppPreference'](student_id=g.student.id,phone='')
            payment.enabled=request.form.get('payment_whatsapp')=='yes'
            if payment.enabled:
                if not re.fullmatch(r'\+[1-9][0-9]{6,14}',phone):
                    db.session.rollback();flash('Enter your own WhatsApp number with country code.');return redirect(url_for('connected_accounts'))
                payment.phone=phone;payment.consented_at=now()
            db.session.add(payment)
            db.session.add(alerts);db.session.commit();flash('Class reminder preference saved.')
            return redirect(url_for('connected_accounts'))
        pref=db.session.get(Preference,g.student.id)
        return render_template('connected_accounts.html',providers=[(provider,CATALOG[provider],connection_status(provider),ready(provider)) for provider in ('google','microsoft')],reminders=pref.reminders if pref else True,whatsapp_pref=db.session.get(m['ClassWhatsAppPreference'],g.student.id),payment_whatsapp_pref=db.session.get(m['TuitionWhatsAppPreference'],g.student.id))

    @app.post('/connections/<provider>/start')
    @student_required()
    def calendar_start(provider):
        if provider not in ('google','microsoft'):abort(404)
        try:
            if not ready(provider):raise ValueError('The academy must finish this calendar provider’s setup first.')
            definition=calendar_definition(provider);raw=secrets.token_urlsafe(32);digest=hashlib.sha256(raw.encode()).hexdigest();verifier=secrets.token_urlsafe(48)
            row=db.session.get(Setting,provider)
            state=State(digest=digest,student_id=g.student.id,provider=provider,ciphertext=seal(provider,g.student.id,{'verifier':verifier}),revision=row.revision if row else 0,expires_at=now()+timedelta(minutes=15))
            db.session.add(state);db.session.commit()
            states=dict(session.get('calendar_states',{}));states[provider]=digest;session['calendar_states']=states
            callback=ORIGIN+url_for('calendar_callback',provider=provider)
            args=dict(client_id=definition['client_id'],redirect_uri=callback,response_type='code',scope=definition['scope'],state=raw,code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(),code_challenge_method='S256')
            if provider=='google':args.update(access_type='offline',prompt='consent')
            return redirect(definition['authorize']+'?'+urlencode(args))
        except ValueError as error:flash(str(error));return redirect(url_for('connected_accounts'))

    @app.get('/connections/<provider>/callback')
    @student_required()
    def calendar_callback(provider):
        if provider not in ('google','microsoft'):abort(404)
        raw=request.args.get('state','')
        if not raw or len(raw)>200:abort(400)
        digest=hashlib.sha256(raw.encode()).hexdigest()
        if not hmac.compare_digest(session.get('calendar_states',{}).get(provider,''),digest):abort(403)
        state=db.session.scalar(select(State).where(State.digest==digest).with_for_update())
        row=db.session.get(Setting,provider)
        if not state or state.student_id!=g.student.id or state.provider!=provider or state.consumed or aware(state.expires_at)<now() or state.revision!=(row.revision if row else 0) or not ready(provider):abort(403)
        verifier=unseal(provider,g.student.id,state.ciphertext)['verifier'];state.consumed=True;db.session.commit()
        states=dict(session.get('calendar_states',{}));states.pop(provider,None);session['calendar_states']=states
        if request.args.get('error'):
            audit(provider,'student_authorization','cancelled');db.session.commit();flash('Calendar authorization was cancelled. No calendar account was connected.');return redirect(url_for('connected_accounts'))
        try:
            code=request.args.get('code','')
            if not code or len(code)>5000:raise ValueError('The provider did not return an authorization code.')
            definition=calendar_definition(provider)
            tokens=transport(definition['token'],'POST',dict(client_id=definition['client_id'],client_secret=definition['client_secret'],grant_type='authorization_code',code=code,redirect_uri=ORIGIN+url_for('calendar_callback',provider=provider),code_verifier=verifier),form=True)
            access=tokens.get('access_token');refresh=tokens.get('refresh_token')
            if not isinstance(access,str) or not access or not isinstance(refresh,str) or not refresh:raise ValueError('Offline calendar permission was not granted. Reconnect and approve the requested calendar access.')
            headers={'Authorization':'Bearer '+access}
            # Each student owns a separate LionTech calendar. No shared app token impersonates students.
            if provider=='google':calendar=transport('https://www.googleapis.com/calendar/v3/calendars','POST',{'summary':'LionTech Academy'},headers)
            else:calendar=transport('https://graph.microsoft.com/v1.0/me/calendars','POST',{'name':'LionTech Academy'},headers)
            if not isinstance(calendar.get('id'),str) or not calendar['id']:raise ValueError('The provider did not confirm the LionTech calendar.')
            values={'access_token':access,'refresh_token':refresh,'expires_at':(now()+timedelta(seconds=min(max(int(tokens.get('expires_in',3600)),60),86400))).isoformat(),'calendar_id':calendar['id']}
            connection=db.session.get(Connection,(g.student.id,provider)) or Connection(student_id=g.student.id,provider=provider)
            connection.ciphertext=seal(provider,g.student.id,values);connection.generation=generation(provider);connection.status='connected';connection.updated_at=now();db.session.add(connection)
            audit(provider,'student_authorization','connected');db.session.commit();dispatch();flash('Calendar connected with your permission. Use Sync my classes to add or update your eligible sessions.')
        except Exception:
            db.session.rollback();audit(provider,'student_authorization','failed');db.session.commit();flash('The provider could not complete calendar authorization. Check consent, app verification and callback settings, then reconnect.')
        return redirect(url_for('connected_accounts'))

    def calendar_tokens(connection):
        if not ready(connection.provider) or connection.generation!=generation(connection.provider):raise ValueError('Calendar reconnection is required.')
        values=unseal(connection.provider,connection.student_id,connection.ciphertext)
        if m['datetime'].fromisoformat(values['expires_at'])<=now()+timedelta(minutes=2):
            definition=calendar_definition(connection.provider)
            payload=dict(client_id=definition['client_id'],client_secret=definition['client_secret'],grant_type='refresh_token',refresh_token=values['refresh_token'])
            tokens=transport(definition['token'],'POST',payload,form=True)
            if not tokens.get('access_token'):raise ValueError('The provider requires calendar authorization again.')
            values.update(access_token=tokens['access_token'],refresh_token=tokens.get('refresh_token') or values['refresh_token'],expires_at=(now()+timedelta(seconds=min(max(int(tokens.get('expires_in',3600)),60),86400))).isoformat())
            connection.ciphertext=seal(connection.provider,connection.student_id,values);db.session.commit()
        return values

    @app.post('/connections/<provider>/disconnect')
    @student_required()
    def calendar_disconnect(provider):
        if provider not in ('google','microsoft'):abort(404)
        connection=db.session.get(Connection,(g.student.id,provider))
        if connection:
            # Removing the local credential ends portal access. Existing calendar entries are retained.
            db.session.delete(connection);audit(provider,'student_disconnect','disconnected');db.session.commit()
        flash('Calendar disconnected from this portal. Existing events are retained. You can also remove the app permission in your provider account.')
        return redirect(url_for('connected_accounts'))

    def sync_calendar(student,provider,limit=10,class_id=None):
        connection=db.session.scalar(select(Connection).where(Connection.student_id==student.id,Connection.provider==provider).with_for_update())
        if not connection or connection.status!='connected':return 0
        values=calendar_tokens(connection);event_generation=hashlib.sha256((connection.generation+values['calendar_id']).encode()).hexdigest();headers={'Authorization':'Bearer '+values['access_token']};calendar=quote(values['calendar_id'],safe='')
        course_ids=list(db.session.scalars(select(m['Enrollment'].course_id).where(m['Enrollment'].student_id==student.id,m['Enrollment'].status=='approved',m['Enrollment'].payment_status=='verified')))
        classes=[lesson for lesson in db.session.scalars(select(m['ClassSession']).where(m['ClassSession'].course_id.in_(course_ids),m['ClassSession'].starts_at>=now()-timedelta(days=1)).order_by(m['ClassSession'].starts_at)) if app.extensions['global_experience']['class_visible'](lesson,student)]
        if class_id is not None:classes=[lesson for lesson in classes if lesson.id==class_id]
        sent=0
        for lesson in classes:
            detail=db.session.get(m['ScheduledClass'],lesson.id);revision=detail.revision if detail else 0
            link=db.session.get(Link,(student.id,provider,lesson.id))
            if link and link.generation==event_generation and link.revision==revision:continue
            start=aware(lesson.starts_at);end=start+timedelta(minutes=detail.duration if detail else 120)
            description=lesson.course.name+' — View class / Join: '+ORIGIN+'/classes/'+str(lesson.id)+' (student sign-in and eligible enrollment required)'
            stable=hashlib.sha256((student.id+provider+str(lesson.id)+event_generation).encode()).hexdigest()
            if provider=='google':
                eid='lt'+stable;url='https://www.googleapis.com/calendar/v3/calendars/'+calendar+'/events'
                body=dict(summary=lesson.title,description=description,start={'dateTime':start.isoformat()},end={'dateTime':end.isoformat()})
                try:result=transport(url+'/'+eid,'PUT',body,headers)
                except HTTPError as error:
                    if error.code!=404:raise
                    try:result=transport(url,'POST',dict(id=eid,**body),headers)
                    except HTTPError as conflict:
                        if conflict.code!=409:raise
                        result=transport(url+'/'+eid,'PUT',body,headers)
            else:
                url='https://graph.microsoft.com/v1.0/me/calendars/'+calendar+'/events'
                body=dict(subject=lesson.title,body={'contentType':'text','content':description},start={'dateTime':start.strftime('%Y-%m-%dT%H:%M:%S'),'timeZone':'UTC'},end={'dateTime':end.strftime('%Y-%m-%dT%H:%M:%S'),'timeZone':'UTC'})
                if link and link.generation==event_generation:result=transport(url+'/'+quote(link.event_id,safe=''),'PATCH',body,headers)
                else:result=transport(url,'POST',dict(transactionId=stable,**body),headers)
            eid=result.get('id') or (link.event_id if link else None)
            if not isinstance(eid,str) or not eid:raise ValueError('The provider did not confirm this calendar event.')
            link=link or Link(student_id=student.id,provider=provider,class_id=lesson.id)
            link.event_id,link.revision,link.generation=eid,revision,event_generation;db.session.add(link);db.session.commit();sent+=1
            if sent>=limit:break
        return sent

    @app.post('/connections/<provider>/sync')
    @student_required()
    def calendar_sync(provider):
        if provider not in ('google','microsoft'):abort(404)
        try:
            count=sync_calendar(g.student,provider);audit(provider,'student_calendar_sync','success');db.session.commit();flash(f'{count} class event(s) added or updated. Existing event links are reused.')
        except Exception:
            db.session.rollback();audit(provider,'student_calendar_sync','failed');db.session.commit();flash('Calendar sync did not complete. Check the connection or reconnect, then retry.')
        return redirect(url_for('connected_accounts'))

    Control=m['IntegrationDeliveryControl']

    def dispatch(probe=False,class_id=None):
        if app.testing and not app.config.get('DIRECT_DELIVERY'):return True
        # Complete direct provider calls before returning the scheduling response.
        # Independent sessions let a small pool deliver to students concurrently.
        from concurrent.futures import ThreadPoolExecutor
        ids=[];cursor=0
        while True:
            batch=delivery_batch(cursor,100,class_id=class_id)
            ids.extend(batch)
            if len(batch)<100:break
            cursor=batch[-1]
        db.session.commit()
        def deliver(identity):
            with app.app_context():
                try:process_delivery(identity);return True
                except Exception:
                    db.session.rollback()
                    app.logger.warning('Direct class delivery failed; delivery record retained.')
                    return False
        if app.testing:results=[deliver(identity) for identity in ids]
        else:
            with ThreadPoolExecutor(max_workers=8) as executor:results=list(executor.map(deliver,ids))
        control=db.session.get(Control,1) or Control(id=1)
        control.automatic=True;control.last_publish_at=now();control.publish_status='direct_completed' if all(results) else 'direct_partial_failure'
        db.session.add(control);audit('delivery','direct_send',control.publish_status);db.session.commit()
        return all(results)

    @app.post('/admin/integrations/delivery/settings')
    @admin_required
    def integration_delivery_settings():
        reauthenticate()
        success=dispatch()
        flash('Direct delivery completed. Review notification activity for provider status.' if success else 'Some deliveries failed. Review activity and provider settings before retrying.')
        return redirect(url_for('integration_hub'))

    @app.context_processor
    def integration_context():
        return {'delivery_control':db.session.get(Control,1) if session.get('admin') else None}

    def delivery_batch(cursor,limit=30,class_id=None):
        Job=m['ClassDelivery'];Class=m['ClassSession']
        query=select(Job.id).join(Class,Class.id==Job.class_id).where(Job.id>cursor,Class.starts_at>=now()-timedelta(days=1))
        if class_id is not None:query=query.where(Job.class_id==class_id)
        return list(db.session.scalars(query.order_by(Job.id).limit(limit)))

    def process_delivery(job_id):
        Job=m['ClassDelivery'];Enrollment=m['Enrollment'];Detail=m['ScheduledClass']
        job=db.session.scalar(select(Job).where(Job.id==job_id).with_for_update().execution_options(populate_existing=True))
        if not job:return
        lesson=job.lesson;detail=db.session.get(Detail,lesson.id)
        enrollment=db.session.scalar(select(Enrollment).where(Enrollment.student_id==job.student_id,Enrollment.course_id==lesson.course_id,Enrollment.status=='approved',Enrollment.payment_status=='verified'))
        student_control=db.session.get(m['StudentControl'],job.student_id)
        if (student_control and not student_control.active) or not enrollment or (detail and (not detail.published or detail.revision!=job.revision or (detail.cohort_id and detail.cohort_id!=enrollment.cohort_id))):
            job.status='superseded';db.session.commit();return
        pref=db.session.get(Preference,job.student_id)
        if job.status!='sent' and ready('email') and (not pref or pref.reminders) and job.attempts<5:
            job.attempts+=1
            try:
                from providers import send_class_email
                app.config.get('CLASS_EMAIL_SENDER',send_class_email)(job.student,lesson,app.extensions['global_experience']['calendar_text'](lesson,job.student))
                job.status='sent';audit('email','class_update','sent');db.session.commit()
            except Exception:
                job.status='failed';audit('email','class_update','failed');db.session.commit();raise RuntimeError('Class email delivery failed.') from None
        else:db.session.commit()
        for provider in ('google','microsoft'):
            if ready(provider) and db.session.get(Connection,(job.student_id,provider)):
                try:
                    count=sync_calendar(job.student,provider,limit=1,class_id=lesson.id)
                    if count:audit(provider,'class_calendar_update','synced');db.session.commit()
                except Exception:
                    db.session.rollback();audit(provider,'class_calendar_update','failed');db.session.commit();raise RuntimeError('Class calendar update failed.') from None

    def worker_probe():
        control=db.session.get(Control,1) or Control(id=1)
        control.worker_checked_at=now();control.publish_status='worker_confirmed';db.session.add(control);db.session.commit()

    return dict(reauthenticate=reauthenticate,config=config,ready=ready,seal=seal,unseal=unseal,sync_calendar=sync_calendar,generation=generation,dispatch=dispatch,delivery_batch=delivery_batch,process_delivery=process_delivery,worker_probe=worker_probe)
