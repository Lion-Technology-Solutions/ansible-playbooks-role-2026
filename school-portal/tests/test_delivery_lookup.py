import io
from urllib.error import HTTPError
import pytest
from sqlalchemy import select
from app import db,LifecycleEvent
from test_portal import admin,register,csrf
from test_readability_notifications import credentials


def event(app,manager,monkeypatch,sid):
    student=app.test_client();register(student)
    for key,value in credentials().items():monkeypatch.setenv(key,value)
    with app.app_context():
        row=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='account_created',LifecycleEvent.channel=='whatsapp'))
        row.status='accepted';row.provider_id=sid;db.session.commit();return row.id


@pytest.mark.parametrize('prefix',['SM','MM'])
def test_delivery_lookup_supports_sms_and_multimedia_message_ids(app,monkeypatch,prefix):
    manager=app.test_client();admin(manager);identity=event(app,manager,monkeypatch,prefix+'a'*32)
    seen=[];app.config['LIFECYCLE_HTTP']=lambda *args,**kwargs:(seen.append((args,kwargs)) or {'status':'delivered','num_media':'0','error_code':None})
    response=manager.post('/admin/notifications',data={'csrf':csrf(manager,'/admin/notifications'),'admin_password':'test-admin-password','action':'refresh_delivery'},follow_redirects=True)
    assert response.status_code==200 and b'Delivered' in response.data and b'Media items: 0' in response.data
    assert len(seen)==1 and '/Messages/'+prefix+'a'*32+'.json' in seen[0][0][0]
    with app.app_context():assert db.session.get(LifecycleEvent,identity).status=='accepted'


def test_lookup_errors_and_missing_references_are_visible_not_silently_skipped(app,monkeypatch):
    manager=app.test_client();admin(manager);identity=event(app,manager,monkeypatch,'MM'+'b'*32)
    def failed(*args,**kwargs):raise HTTPError('https://api.twilio.com/',401,'unauthorized',{},io.BytesIO(b'private-provider-response'))
    app.config['LIFECYCLE_HTTP']=failed
    response=manager.post('/admin/notifications',data={'csrf':csrf(manager,'/admin/notifications'),'admin_password':'test-admin-password','action':'refresh_delivery'},follow_redirects=True)
    assert b'Lookup HTTP 401' in response.data and b'Twilio rejected authentication' in response.data
    assert b'private-provider-response' not in response.data
    with app.app_context():row=db.session.get(LifecycleEvent,identity);row.provider_id='';db.session.commit()
    response=manager.post('/admin/notifications',data={'csrf':csrf(manager,'/admin/notifications'),'admin_password':'test-admin-password','action':'refresh_delivery'},follow_redirects=True)
    assert b'Reference unavailable' in response.data and b'1 missing a message reference' in response.data


def test_admin_page_automatically_reads_latest_status_without_resending(app,monkeypatch):
    manager=app.test_client();admin(manager);identity=event(app,manager,monkeypatch,'MM'+'c'*32)
    app.config['AUTO_CHECK_WHATSAPP_DELIVERY']=True
    seen=[];app.config['LIFECYCLE_HTTP']=lambda *args,**kwargs:(seen.append(args) or {'status':'undelivered','num_media':'0','error_code':63016})
    for _ in range(2):
        page=manager.get('/admin/notifications')
        assert b'Undelivered' in page.data and b'Error 63016' in page.data
    assert len(seen)==1 and len(seen[0])==1  # GET only; cached second view; no Messages POST
    assert app.test_client().get('/admin/notifications').status_code==302


def test_unconfigured_connection_shows_action_required_instead_of_not_checked(app,monkeypatch):
    manager=app.test_client();admin(manager);identity=event(app,manager,monkeypatch,'MM'+'d'*32)
    monkeypatch.delenv('TWILIO_AUTH_TOKEN')
    app.config['AUTO_CHECK_WHATSAPP_DELIVERY']=True
    page=manager.get('/admin/notifications')
    assert b'Connection check required' in page.data and b'run Test connection' in page.data
