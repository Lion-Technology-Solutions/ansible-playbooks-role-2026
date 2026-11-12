from datetime import timedelta
from sqlalchemy import select
from app import db, PortalSessionActivity, RolePreviewAudit, now
from test_portal import admin, register, csrf


def setup(app, kind='admin'):
    clock=[now()];app.config['SESSION_CLOCK']=lambda:clock[0]
    client=app.test_client()
    admin(client) if kind=='admin' else register(client)
    return client,clock


def token(client,path='/admin'):
    return csrf(client,path)


def test_fifteen_minutes_idle_expires_admin_and_student_on_server(app):
    for kind in ['admin','student']:
        client,clock=setup(app,kind)
        with client.session_transaction() as state:cookie_nonce=state['portal_session']
        clock[0]+=timedelta(minutes=14,seconds=59)
        assert client.get('/session/status').status_code==200
        clock[0]+=timedelta(seconds=1)
        result=client.get('/session/status')
        assert result.status_code==401 and result.json['reason']=='inactive'
        with client.session_transaction() as state:assert 'admin' not in state and 'student_id' not in state
        import hashlib
        with app.app_context():assert db.session.get(PortalSessionActivity,hashlib.sha256(cookie_nonce.encode()).hexdigest()).revoked


def test_background_reads_do_not_renew_but_explicit_activity_does(app):
    client,clock=setup(app);initial=client.get('/session/status').json
    csrf_token=token(client)
    clock[0]+=timedelta(minutes=14)
    for _ in range(3):assert client.get('/session/status').json['expires_at']==initial['expires_at']
    response=client.post('/session/activity',headers={'X-CSRF-Token':csrf_token})
    assert response.status_code==200 and response.json['expires_at']!=initial['expires_at']
    clock[0]+=timedelta(minutes=14)
    assert client.get('/admin').status_code==200
    clock[0]+=timedelta(minutes=1)
    assert client.get('/session/status').status_code==401


def test_expired_post_is_blocked_and_cannot_revive(app):
    client,clock=setup(app);csrf_token=token(client)
    clock[0]+=timedelta(minutes=15)
    assert client.post('/session/activity',headers={'X-CSRF-Token':csrf_token}).status_code==401
    assert client.post('/admin/profile-switch',data={'csrf':csrf_token,'role':'student'}).status_code in (400,403)
    with app.app_context():assert not db.session.scalar(select(RolePreviewAudit))


def test_logout_revokes_cookie_replay_and_clears_preview(app):
    client,clock=setup(app)
    csrf_token=token(client)
    client.post('/admin/profile-switch',data={'csrf':csrf_token,'role':'student'})
    cookie=client.get_cookie('session').value
    assert client.post('/logout',data={'csrf':csrf_token}).status_code==302
    replay=app.test_client();replay.set_cookie('session',cookie)
    response=replay.get('/admin')
    assert response.status_code==302 and '/admin/login' in response.location
    with replay.session_transaction() as state:assert 'role_preview' not in state and 'admin' not in state


def test_role_previews_are_admin_only_and_live_writes_are_blocked(app):
    client,clock=setup(app);csrf_token=token(client)
    for role in ['student','instructor','employee']:
        response=client.post('/admin/profile-switch',data={'csrf':csrf_token,'role':role},follow_redirects=True)
        assert response.status_code==200 and b'Sample data' in response.data and b'Back to Admin' in response.data
        with client.session_transaction() as state:assert state['admin'] and 'student_id' not in state
        assert client.post('/admin',data={'csrf':csrf_token,'action':'create_lesson'}).status_code==403
        assert client.get('/admin').location.endswith('/admin/preview/'+role)
    assert client.post('/admin/preview/exit',data={'csrf':csrf_token}).location.endswith('/admin')
    with app.app_context():assert len(db.session.scalars(select(RolePreviewAudit)).all())==4
    student=app.test_client();register(student,email='preview-denied@example.com')
    assert student.get('/admin/profile-switch').status_code==403
    assert student.get('/admin/preview/student').status_code==403
    assert student.post('/admin/profile-switch',data={'csrf':csrf(student,'/portal'),'role':'student'}).status_code==403


def test_profile_menu_and_session_messages_are_accessible(app):
    client,clock=setup(app)
    page=client.get('/admin').data
    assert b'data-profile-menu' in page and b'Sign out' in page and b'Switch role preview' in page
    assert b'aria-labelledby="session-warning-title"' in page and b'Stay signed in' in page
    assert b'inactive' in client.get('/admin/login?reason=inactive').data
    assert client.post('/session/activity').status_code==400
    assert client.post('/admin/profile-switch',data={'csrf':token(client),'role':'owner'}).status_code==400


def test_absolute_session_limit_survives_regular_activity(app):
    client,clock=setup(app);csrf_token=token(client)
    for _ in range(23):
        clock[0]+=timedelta(minutes=10)
        assert client.post('/session/activity',headers={'X-CSRF-Token':csrf_token}).status_code==200
    clock[0]+=timedelta(minutes=10)
    assert client.get('/session/status').status_code==401
