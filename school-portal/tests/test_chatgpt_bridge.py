import hashlib,hmac,re
from datetime import timedelta
from sqlalchemy import select
from app import db,PortalAccessKey,AssistantDraft,Course,now
from test_portal import admin,csrf


def key(app,write=True):
    token='ltp_test-private-action-key'
    with app.app_context():
        row=PortalAccessKey(label='Private GPT',digest=hashlib.sha256(token.encode()).hexdigest(),admin_stamp=hmac.new(app.secret_key.encode(),app.config['ADMIN_PASSWORD'].encode(),'sha256').hexdigest(),can_write=write,expires_at=now()+timedelta(days=1))
        db.session.add(row);db.session.commit();identity=row.id
    return {'Authorization':'Bearer '+token},identity


def test_external_api_auth_scope_and_no_student_data(app):
    client=app.test_client();client.get('/')
    assert client.get('/api/chatgpt/context').status_code==401
    headers,identity=key(app,False)
    response=client.get('/api/chatgpt/context',headers=headers)
    assert response.status_code==200 and response.json['programs']
    assert 'students' not in response.json and 'my_account' not in response.json
    assert client.post('/api/chatgpt/drafts',json={},headers=headers).status_code==403
    with app.app_context():db.session.get(PortalAccessKey,identity).revoked=True;db.session.commit()
    assert client.get('/api/chatgpt/context',headers=headers).status_code==401


def test_external_draft_confirm_apply_replay_and_revocation_on_password_change(app):
    client=app.test_client();client.get('/');headers,identity=key(app)
    draft=client.post('/api/chatgpt/drafts',headers=headers,json={'action':'create_course','target':0,'fields':[{'key':k,'value':v} for k,v in {'name':'French Cloud','slug':'french-cloud','description':'A practical cloud program','skills':'AWS','publication_state':'published'}.items()]})
    assert draft.status_code==201
    with app.app_context():assert not db.session.scalar(select(Course).where(Course.slug=='french-cloud'))
    path='/api/chatgpt/drafts/'+draft.json['id']+'/apply'
    assert client.post(path,json={},headers=headers).status_code==400
    result=client.post(path,json={'confirm':True},headers=headers)
    assert result.status_code==200,result.json
    assert result.json['status']=='applied'
    with app.app_context():assert db.session.scalar(select(Course).where(Course.slug=='french-cloud')).publication.published
    assert client.post(path,json={'confirm':True},headers=headers).json['replayed']
    app.config['ADMIN_PASSWORD']='rotated-password'
    assert client.get('/api/chatgpt/context',headers=headers).status_code==401


def test_external_disallows_payments_secrets_foreign_drafts_and_stale_targets(app):
    client=app.test_client();client.get('/');headers,identity=key(app)
    for action in ('verify_payment','reset_password','integration','delete_course'):
        assert client.post('/api/chatgpt/drafts',headers=headers,json={'action':action,'target':1,'fields':[]}).status_code==400
    with app.app_context():cid=db.session.scalar(select(Course.id))
    result=client.post('/api/chatgpt/drafts',headers=headers,json={'action':'edit_course','target':cid,'fields':[{'key':'name','value':'Changed name'}]})
    path='/api/chatgpt/drafts/'+result.json['id']+'/apply'
    with app.app_context():row=db.session.get(Course,cid);row.description='Concurrent staff edit';db.session.commit()
    assert client.post(path,headers=headers,json={'confirm':True}).status_code==409
    with app.app_context():row=db.session.get(AssistantDraft,result.json['id']);row.owner='another-connection';db.session.commit()
    assert client.post(path,headers=headers,json={'confirm':True}).status_code==404


def test_connection_settings_require_admin_reauthentication_and_show_key_once(app):
    client=app.test_client()
    assert client.get('/admin/chatgpt').status_code==302
    admin(client)
    assert client.post('/admin/chatgpt',data={'csrf':csrf(client,'/admin/chatgpt'),'action':'create','label':'GPT','acknowledge':'yes','admin_password':'wrong'}).status_code==403
    result=client.post('/admin/chatgpt',data={'csrf':csrf(client,'/admin/chatgpt'),'action':'create','label':'GPT','acknowledge':'yes','admin_password':'test-admin-password'})
    assert result.status_code==200
    token=re.search(rb'<code translate="no">([^<]+)',result.data)[1].decode()
    assert token not in client.get('/admin/chatgpt').text
    with app.app_context():assert db.session.scalar(select(PortalAccessKey)).digest==hashlib.sha256(token.encode()).hexdigest()
    schema=client.get('/api/chatgpt/openapi.json').json
    assert schema['paths']['/api/chatgpt/drafts/{draft_id}/apply']['post']['x-openai-isConsequential'] is True


def test_expired_keys_and_unconfirmed_or_invalid_changes_do_not_mutate(app):
    client=app.test_client();client.get('/');headers,identity=key(app)
    with app.app_context():
        course=db.session.scalar(select(Course));cid=course.id;before=course.name
    draft=client.post('/api/chatgpt/drafts',headers=headers,json={'action':'edit_course','target':cid,'fields':[{'key':'name','value':''}]})
    result=client.post('/api/chatgpt/drafts/'+draft.json['id']+'/apply',headers=headers,json={'confirm':True})
    assert result.status_code==422
    with app.app_context():
        assert db.session.get(Course,cid).name==before
        assert db.session.get(AssistantDraft,draft.json['id']).status=='pending'
        db.session.get(PortalAccessKey,identity).expires_at=now()-timedelta(seconds=1);db.session.commit()
    assert client.get('/api/chatgpt/context',headers=headers).status_code==401
