import pytest
from sqlalchemy import select
from app import db,Student,LifecycleEvent,AdminAccount
from whatsapp_templates import validate,TemplateError
from test_portal import admin,register,csrf


def credentials():return {'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'unit','TWILIO_WHATSAPP_FROM':'whatsapp:+19152219465','TWILIO_TEMPLATE_SID':'HX'+'a'*32}


def test_validator_accepts_only_approved_text_with_matching_variables(app):
    seen=[]
    def transport(url,**kwargs):
        seen.append(url)
        return {'whatsapp':{'status':'approved','content_type':'twilio/text'}} if url.endswith('/ApprovalRequests') else {'types':{'twilio/text':{'body':'Student: {{1}}. Status: {{2}}. Review: {{3}}. Please sign in.'}}}
    with app.app_context():
        for _ in range(2):assert validate(credentials(),'HX'+'a'*32,{'1':'student','2':'review','3':'https://www.liontechacademy.com/admin'},transport)['type']=='twilio/text'
    assert len(seen)==2  # metadata checks are cached, without caching message bodies or recipient data


@pytest.mark.parametrize('types,status,expected',[
    ({'twilio/media':{'body':'Student {{1}}','media':['https://example.com/photo.png']}},'approved','unsupported_whatsapp_template'),
    ({'twilio/text':{'body':'Student {{1}}'},'twilio/call-to-action':{}},'approved','unsupported_whatsapp_template'),
    ({'twilio/text':{'body':'Student {{1}}'}},'received','template_approval_pending'),
    ({'twilio/text':{'body':'Student {{2}}'}},'approved','template_fields_mismatch')])
def test_template_validation_blocks_media_actions_pending_approval_and_wrong_fields(app,types,status,expected):
    def transport(url,**kwargs):return {'whatsapp':{'status':status,'content_type':'twilio/text'}} if url.endswith('/ApprovalRequests') else {'types':types}
    with app.app_context(),pytest.raises(TemplateError) as error:validate(credentials(),'HX'+'a'*32,{'1':'student'},transport)
    assert error.value.status==expected


def test_account_review_email_fallback_and_plain_whatsapp_payload(app,monkeypatch):
    student=app.test_client();register(student)
    for key,value in {**credentials(),'SENDGRID_API_KEY':'unit','SENDGRID_FROM_EMAIL':'notices@example.com'}.items():monkeypatch.setenv(key,value)
    sent=[]
    app.config['LIFECYCLE_HTTP']=lambda *args,**kwargs:(sent.append((args,kwargs)) or {'sid':'SM'+'b'*32})
    app.config['WHATSAPP_TEMPLATE_VALIDATOR']=validate
    app.config['WHATSAPP_TEMPLATE_HTTP']=lambda url,**kwargs:({'whatsapp':{'status':'approved','content_type':'twilio/text'}} if url.endswith('/ApprovalRequests') else {'types':{'twilio/text':{'body':'Student {{1}}. Status {{2}}. Link {{3}}. Sign in.'}}})
    with app.test_request_context('/'):
        support=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='support_account_created'))
        whatsapp=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='account_created',LifecycleEvent.channel=='whatsapp'))
        app.extensions['notifications']['process'](support.id);app.extensions['notifications']['process'](whatsapp.id)
        assert support.status=='accepted' and whatsapp.status=='accepted'
    email=sent[0][0][2];message=sent[1][0][2]
    assert email['personalizations'][0]['to'][0]['email']=='liontechacademy@gmail.com'
    assert '/admin/students/' in email['content'][0]['value']
    assert 'attachments' not in email
    assert set(message)=={'From','To','ContentSid','ContentVariables'} and message['To']=='whatsapp:+14375756656'


def test_incompatible_template_cannot_send_and_delivery_check_reads_only(app,monkeypatch):
    manager,student=app.test_client(),app.test_client();admin(manager);register(student)
    for key,value in credentials().items():monkeypatch.setenv(key,value)
    app.config['WHATSAPP_TEMPLATE_VALIDATOR']=validate
    app.config['WHATSAPP_TEMPLATE_HTTP']=lambda *args,**kwargs:{'types':{'twilio/media':{'media':['https://example.com/receipt.pdf']}}}
    calls=[];app.config['LIFECYCLE_HTTP']=lambda *args,**kwargs:(calls.append((args,kwargs)) or {'status':'delivered','num_media':'0','error_code':None})
    with app.app_context():
        row=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='account_created',LifecycleEvent.channel=='whatsapp'));identity=row.id
        app.extensions['notifications']['process'](identity)
        assert row.status=='unsupported_whatsapp_template' and row.attempts==0 and not calls
        row.provider_id='SM'+'c'*32;row.status='accepted';db.session.commit()
    response=manager.post('/admin/notifications',data={'csrf':csrf(manager,'/admin/notifications'),'admin_password':'test-admin-password','action':'refresh_delivery'},follow_redirects=True)
    assert response.status_code==200 and b'Delivered' in response.data and b'Media items: 0' in response.data
    assert len(calls)==1 and calls[0][0][0].endswith('/Messages/SM'+'c'*32+'.json') and 'method' not in calls[0][1]


def test_public_reading_controls_and_professional_signin_copy(app):
    client=app.test_client()
    page=client.get('/student/login').data
    assert b'Student sign in' in page and b'Keep moving forward' not in page
    assert b'readability.css' in page and b'reading.js' in page
    assert b'data-reading-size-button="large"' in page
    catalog=client.get('/i18n/catalog').json['fr'];assert catalog['Larger text']=='Texte agrandi'
    register(client)
    page=client.get('/verify-email').data
    assert b'verification-code-help' in page and b'Contact Student Support' in page
