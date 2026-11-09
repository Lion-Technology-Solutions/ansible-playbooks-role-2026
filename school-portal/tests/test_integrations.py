import asyncio
import hashlib
import io
import json
from datetime import timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qs,urlsplit
from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select
from app import (db,IntegrationSetting,IntegrationAudit,StudentConnection,CalendarOAuthState,
                 IntegrationDeliveryControl,CalendarEventLink,StudentIntegrationPreference,ClassDelivery,
                 Student,ClassSession,ScheduledClass,now)
from integration_catalog import CATALOG
from integrations import credential
from test_portal import admin,register,analytics,approve,csrf,post
from test_global_portal import schedule


def setup(app):
    app.config['INTEGRATIONS_ENCRYPTION_KEY']=Fernet.generate_key().decode()
    manager,student=app.test_client(),app.test_client();admin(manager);register(student)
    return manager,student


def values_for(provider):
    values={item['key']:'unit-'+item['key'].lower() for item in CATALOG[provider]['fields'] if item['required']}
    values.update({key:value for key,value in {'SENDGRID_FROM_EMAIL':'notifications@example.com','MICROSOFT_TENANT':'common','TWILIO_WHATSAPP_FROM':'whatsapp:+19152219465','YOUTUBE_CHANNEL_ID':'UCunit_channel'}.items() if key in values})
    return values


def configure(app,manager,provider,values=None,action='save',**extra):
    with app.app_context():row=db.session.get(IntegrationSetting,provider);revision=row.revision if row else 0
    fields={'csrf':csrf(manager,'/admin/integrations/'+provider),'admin_password':'test-admin-password','action':action,'revision':str(revision)}
    if action=='save':fields.update(values or values_for(provider))
    fields.update(extra)
    return manager.post('/admin/integrations/'+provider,data=fields,follow_redirects=True)


def http_ok(url,method='GET',payload=None,headers=None,form=False):
    if 'api.openai.com/v1/models/' in url:return {'id':url.rsplit('/',1)[-1]}
    if 'api.stripe.com' in url:return {'id':'acct_unit','charges_enabled':True}
    if url=='https://zoom.us/oauth/token':return {'access_token':'unit_zoom_access'}
    if 'api.zoom.us' in url:return {'id':'unit_host'}
    if 'sendgrid.com' in url:return {'scopes':['mail.send']}
    if 'flutterwave.com' in url:return {'status':'success','data':[]}
    if 'api.twilio.com' in url:return {'sid':values_for('whatsapp')['TWILIO_ACCOUNT_SID'],'status':'active'}
    if 'youtube/v3' in url:return {'items':[{'id':'UCunit_channel'}]}
    if 'oauth/access_token' in url:return {'access_token':'unit_meta_access'}
    if 'graph.facebook.com' in url:return {'id':values_for('instagram')['INSTAGRAM_BUSINESS_ID']}
    if 'linkedin.com' in url:return {'sub':'unit_profile'}
    raise AssertionError('Unexpected provider request')


def test_hub_catalog_filters_admin_boundary_and_no_sample_connections(app):
    manager,student=setup(app)
    page=manager.get('/admin/integrations');assert page.status_code==200
    for provider in CATALOG:assert CATALOG[provider]['name'].encode() in page.data
    assert b'Connection verified' not in page.data
    assert b'Stripe' in manager.get('/admin/integrations?category=payments').data
    assert b'Zoom</h2>' not in manager.get('/admin/integrations?category=payments').data
    assert b'Google Calendar' in manager.get('/admin/integrations?q=calendar').data
    assert manager.get('/admin/integrations?category=invalid').status_code==400
    assert student.get('/admin/integrations').status_code==302
    assert app.test_client().get('/admin/integrations/stripe').status_code==302
    assert manager.get('/admin/integrations/unknown').status_code==404


def test_secrets_encrypted_masked_bound_and_mutations_need_reauthentication(app):
    manager,student=setup(app);secret='CONFIDENTIAL-STRIPE-SECRET-UNIT'
    vals=values_for('stripe');vals['STRIPE_SECRET_KEY']=secret
    r=configure(app,manager,'stripe',vals,admin_password='wrong')
    assert r.status_code==403
    with app.app_context():assert db.session.get(IntegrationSetting,'stripe') is None
    r=configure(app,manager,'stripe',vals);assert r.status_code==200 and secret.encode() not in r.data
    with app.app_context():
        row=db.session.get(IntegrationSetting,'stripe');assert secret not in row.ciphertext
        service=app.extensions['integrations'];assert service['config']('stripe')['STRIPE_SECRET_KEY']==secret
        with pytest.raises(ValueError):service['unseal']('zoom','academy',row.ciphertext)
        with pytest.raises(ValueError):service['unseal']('stripe','someone-else',row.ciphertext)
    # Blank fields preserve the existing secret; the browser never gets its value.
    configure(app,manager,'stripe',{'STRIPE_SECRET_KEY':'','STRIPE_WEBHOOK_SECRET':''})
    with app.app_context():assert app.extensions['integrations']['config']('stripe')['STRIPE_SECRET_KEY']==secret
    assert secret.encode() not in manager.get('/admin/integrations/activity').data
    assert manager.post('/admin/integrations/stripe',data={'action':'pause','admin_password':'test-admin-password'}).status_code==400
    assert student.post('/admin/integrations/stripe',data={'csrf':csrf(student,'/portal'),'action':'save','admin_password':'test-admin-password',**vals}).status_code==302


def test_connection_checks_all_catalog_providers_are_real_and_failures_are_redacted(app):
    manager,student=setup(app);app.config['INTEGRATION_HTTP']=http_ok
    for provider in CATALOG:
        vals=values_for(provider)
        if provider=='linkedin':vals['LINKEDIN_ACCESS_TOKEN']='unit-linkedin-token'
        assert configure(app,manager,provider,vals).status_code==200
        assert configure(app,manager,provider,action='test').status_code==200
        with app.app_context():
            row=db.session.get(IntegrationSetting,provider)
            assert row.status==('oauth_ready' if provider in ('google','microsoft') else 'connected')
    def failure(*args,**kwargs):raise HTTPError('https://provider.invalid?secret=LEAK-ME',403,'LEAK-ME',{},io.BytesIO(b'LEAK-ME'))
    app.config['INTEGRATION_HTTP']=failure
    page=configure(app,manager,'stripe',action='test')
    assert b'LEAK-ME' not in page.data and b'HTTP 403' in page.data
    with app.app_context():assert db.session.get(IntegrationSetting,'stripe').status=='error'
    assert b'LEAK-ME' not in manager.get('/admin/integrations/activity').data


def test_saved_settings_feed_runtime_adapters_and_pause_keeps_webhook_validation(app,monkeypatch):
    manager,student=setup(app);app.config['INTEGRATION_HTTP']=http_ok
    configure(app,manager,'stripe');configure(app,manager,'stripe',action='test')
    import providers
    seen=[]
    monkeypatch.setattr(providers,'api',lambda url,method='GET',payload=None,headers=None,form=False:seen.append(headers) or {'ok':True})
    with app.test_request_context('/health'):
        assert app.extensions['integrations']['ready']('stripe')
        providers.stripe_request('account')
    assert seen[0]['Authorization']=='Bearer '+values_for('stripe')['STRIPE_SECRET_KEY']
    configure(app,manager,'stripe',action='pause')
    with app.test_request_context('/health'):
        assert not app.extensions['integrations']['ready']('stripe')
        assert credential('STRIPE_SECRET_KEY')==''
    with app.test_request_context('/webhooks/stripe',method='POST'):
        assert credential('STRIPE_SECRET_KEY')==values_for('stripe')['STRIPE_SECRET_KEY']
    configure(app,manager,'stripe',action='resume')
    with app.test_request_context('/health'):assert app.extensions['integrations']['ready']('stripe')
    # Optimistic revision prevents another browser tab overwriting a newer secret.
    result=manager.post('/admin/integrations/stripe',data={'csrf':csrf(manager,'/admin/integrations/stripe'),'action':'pause','revision':'1','admin_password':'test-admin-password'},follow_redirects=True)
    assert b'another session' in result.data


def setup_oauth(app,manager,provider):
    configure(app,manager,provider);configure(app,manager,provider,action='test')
    calls=[]
    def http(url,method='GET',payload=None,headers=None,form=False):
        calls.append((url,method,payload,headers))
        if 'token' in url:return {'access_token':'PRIVATE-ACCESS-TOKEN','refresh_token':'PRIVATE-REFRESH-TOKEN','expires_in':3600}
        if method=='POST' and url.endswith('/calendars'):return {'id':'student-calendar-id'}
        if '/events' in url:return {'id':(payload or {}).get('id','calendar-event-id')}
        raise AssertionError('Unexpected calendar request')
    app.config['INTEGRATION_HTTP']=http
    return calls


def start(client,provider):
    response=client.post('/connections/'+provider+'/start',data={'csrf':csrf(client,'/account/connections')})
    assert response.status_code==302
    args=parse_qs(urlsplit(response.location).query);return args


@pytest.mark.parametrize('provider',['google','microsoft'])
def test_calendar_oauth_pkce_session_binding_single_use_and_encrypted_tokens(app,provider):
    manager,student=setup(app);calls=setup_oauth(app,manager,provider)
    args=start(student,provider);state=args['state'][0]
    assert args['code_challenge_method']==['S256'] and args['redirect_uri']==['https://liontech-student-portal.vercel.app/connections/'+provider+'/callback']
    other=app.test_client();register(other,'other-calendar@example.com')
    callback='/connections/'+provider+'/callback?state='+state+'&code=provider_code'
    assert other.get(callback).status_code==403
    result=student.get(callback,follow_redirects=True);assert b'Calendar connected with your permission' in result.data
    assert b'PRIVATE-ACCESS-TOKEN' not in result.data and b'PRIVATE-REFRESH-TOKEN' not in result.data
    assert student.get(callback).status_code==403
    with app.app_context():
        row=db.session.scalar(select(StudentConnection));assert 'PRIVATE-ACCESS-TOKEN' not in row.ciphertext
        saved=app.extensions['integrations']['unseal'](provider,row.student_id,row.ciphertext)
        assert saved['calendar_id']=='student-calendar-id'
        assert db.session.scalar(select(CalendarOAuthState)).consumed
    token_call=next(call for call in calls if 'token' in call[0]);assert token_call[2]['code_verifier']
    assert hashlib.sha256(token_call[2]['code_verifier'].encode()).digest()
    student.post('/connections/'+provider+'/disconnect',data={'csrf':csrf(student,'/account/connections')})
    with app.app_context():assert not db.session.scalar(select(StudentConnection))


def test_oauth_expiry_provider_error_and_changed_configuration_do_not_link(app):
    manager,student=setup(app);setup_oauth(app,manager,'google')
    args=start(student,'google')
    with app.app_context():row=db.session.scalar(select(CalendarOAuthState));row.expires_at=now()-timedelta(minutes=1);db.session.commit()
    assert student.get('/connections/google/callback?state='+args['state'][0]+'&code=x').status_code==403
    args=start(student,'google');configure(app,manager,'google')
    assert student.get('/connections/google/callback?state='+args['state'][0]+'&code=x').status_code==403
    configure(app,manager,'google',action='test');args=start(student,'google')
    result=student.get('/connections/google/callback?state='+args['state'][0]+'&error=access_denied',follow_redirects=True)
    assert b'authorization was cancelled' in result.data
    with app.app_context():assert not db.session.scalar(select(StudentConnection))


def test_calendar_event_scope_idempotent_updates_and_background_preference(app):
    manager,student=setup(app);calls=setup_oauth(app,manager,'google')
    args=start(student,'google');student.get('/connections/google/callback?state='+args['state'][0]+'&code=x')
    course,cohort=analytics(app);approve(app,student,manager,course,cohort);schedule(manager,course,cohort)
    response=student.post('/connections/google/sync',data={'csrf':csrf(student,'/account/connections')},follow_redirects=True)
    assert b'1 class event(s)' in response.data
    response=student.post('/connections/google/sync',data={'csrf':csrf(student,'/account/connections')},follow_redirects=True)
    assert b'0 class event(s)' in response.data
    event_calls=[call for call in calls if '/events' in call[0]];assert len(event_calls)==1
    assert 'private-class' not in json.dumps(event_calls)
    with app.app_context():
        link=db.session.scalar(select(CalendarEventLink));event_id=link.event_id;lesson=db.session.scalar(select(ClassSession));lid=lesson.id
    schedule(manager,course,cohort,class_id=lid,title='Updated SQL class')
    student.post('/connections/google/sync',data={'csrf':csrf(student,'/account/connections')})
    with app.app_context():assert db.session.scalar(select(CalendarEventLink)).event_id==event_id
    assert [call for call in calls if '/events' in call[0]][-1][2]['summary']=='Updated SQL class'
    post(manager,'/admin',action='reject',enrollment_id=1)
    schedule(manager,course,cohort,class_id=lid,title='Revoked class content')
    count=len(calls);student.post('/connections/google/sync',data={'csrf':csrf(student,'/account/connections')});assert len(calls)==count


def test_automatic_delivery_only_after_admin_enables_and_skips_sent_and_revoked(app):
    manager,student=setup(app);app.config['INTEGRATION_HTTP']=http_ok
    configure(app,manager,'email');configure(app,manager,'email',action='test')
    course,cohort=analytics(app);approve(app,student,manager,course,cohort);schedule(manager,course,cohort)
    sent=[];published=[];app.config['CLASS_EMAIL_SENDER']=lambda *args:sent.append(args);app.config['INTEGRATION_QUEUE_PUBLISH']=lambda message:published.append(message)
    with app.test_request_context('/health'):
        jid=db.session.scalar(select(ClassDelivery.id));app.extensions['integrations']['process_delivery'](jid)
    assert not sent
    r=manager.post('/admin/integrations/delivery/settings',data={'csrf':csrf(manager,'/admin/integrations'),'admin_password':'test-admin-password','automatic':'yes'},follow_redirects=True)
    assert r.status_code==200 and published[0]['kind']=='drain'
    with app.test_request_context('/health'):
        app.extensions['integrations']['process_delivery'](jid);app.extensions['integrations']['process_delivery'](jid)
    assert len(sent)==1
    student.post('/account/connections',data={'csrf':csrf(student,'/account/connections')})
    schedule(manager,course,cohort,title='Reminders opted out')
    with app.test_request_context('/health'):
        last=db.session.scalar(select(ClassDelivery.id).order_by(ClassDelivery.id.desc()));app.extensions['integrations']['process_delivery'](last)
        assert not db.session.get(StudentIntegrationPreference,db.session.scalar(select(Student.id))).reminders
    assert len(sent)==1


def test_queue_subscriber_dispatches_private_id_messages_without_credentials(app,monkeypatch):
    import integration_jobs
    from vercel.queue import Message
    seen=[]
    monkeypatch.setattr(integration_jobs,'_invoke',lambda operation,*args:seen.append((operation,args)) or ([] if operation=='delivery_batch' else None))
    # The subscriber receives the SDK message envelope; direct handler calls exercise routing.
    class Envelope:
        def __init__(self,payload):self.payload=payload
    asyncio.run(integration_jobs.deliver(Envelope({'kind':'probe'})))
    asyncio.run(integration_jobs.deliver(Envelope({'kind':'delivery','id':42})))
    asyncio.run(integration_jobs.deliver(Envelope({'kind':'drain','cursor':0,'run':'test-run'})))
    assert [item[0] for item in seen]==['worker_probe','process_delivery','delivery_batch']
    with pytest.raises(ValueError):asyncio.run(integration_jobs.deliver(Envelope({'kind':'delivery','id':'not-a-database-id'})))


def test_embedded_queue_sends_and_consumes_a_real_class_delivery(app,monkeypatch):
    import integration_jobs
    from vercel.queue.embedded import embedded_queue_service
    manager,student=setup(app);app.config['INTEGRATION_HTTP']=http_ok
    configure(app,manager,'email');configure(app,manager,'email',action='test')
    course,cohort=analytics(app);approve(app,student,manager,course,cohort);schedule(manager,course,cohort)
    with app.app_context():db.session.add(IntegrationDeliveryControl(id=1,automatic=True));db.session.commit()
    delivered=[];app.config['CLASS_EMAIL_SENDER']=lambda *args:delivered.append(args)
    async def exercise():
        done=asyncio.Event();loop=asyncio.get_running_loop()
        def invoke(operation,*args):
            with app.test_request_context('/internal/worker'):
                app.preprocess_request();result=app.extensions['integrations'][operation](*args)
            if operation=='process_delivery':loop.call_soon_threadsafe(done.set)
            return result
        monkeypatch.setattr(integration_jobs,'_invoke',invoke)
        async with embedded_queue_service() as service:
            client=service.get_async_client();monkeypatch.setattr(integration_jobs,'send',client.send)
            await client.send(integration_jobs.TOPIC,{'kind':'drain','cursor':0,'run':'embedded-queue-test'})
            await asyncio.wait_for(done.wait(),timeout=8)
    asyncio.run(exercise())
    assert len(delivered)==1
    with app.app_context():assert db.session.scalar(select(ClassDelivery)).status=='sent'


def test_expired_calendar_tokens_refresh_and_reconnect_creates_new_event_binding(app):
    manager,student=setup(app);calls=setup_oauth(app,manager,'microsoft')
    args=start(student,'microsoft');student.get('/connections/microsoft/callback?state='+args['state'][0]+'&code=x')
    course,cohort=analytics(app);approve(app,student,manager,course,cohort);schedule(manager,course,cohort)
    with app.app_context():
        connection=db.session.scalar(select(StudentConnection));service=app.extensions['integrations']
        values=service['unseal']('microsoft',connection.student_id,connection.ciphertext);values['expires_at']=(now()-timedelta(minutes=1)).isoformat();connection.ciphertext=service['seal']('microsoft',connection.student_id,values);db.session.commit()
    student.post('/connections/microsoft/sync',data={'csrf':csrf(student,'/account/connections')})
    refresh=[call for call in calls if 'token' in call[0] and call[2].get('grant_type')=='refresh_token'];assert len(refresh)==1
    with app.app_context():
        connection=db.session.scalar(select(StudentConnection));service=app.extensions['integrations'];old=db.session.scalar(select(CalendarEventLink)).generation
        values=service['unseal']('microsoft',connection.student_id,connection.ciphertext);values['calendar_id']='different-calendar';connection.ciphertext=service['seal']('microsoft',connection.student_id,values);db.session.commit()
    student.post('/connections/microsoft/sync',data={'csrf':csrf(student,'/account/connections')})
    with app.app_context():assert db.session.scalar(select(CalendarEventLink)).generation!=old
    assert any('different-calendar' in call[0] and call[1]=='POST' for call in calls)
