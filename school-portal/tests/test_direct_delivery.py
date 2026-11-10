from sqlalchemy import select
from app import db,LifecycleEvent,Student
from test_portal import register


def test_lifecycle_sends_before_response_without_queue(app,monkeypatch):
    sent=[]
    for key,value in {'SENDGRID_API_KEY':'unit','SENDGRID_FROM_EMAIL':'notices@example.com'}.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr('integration_jobs.publish',lambda *a:(_ for _ in ()).throw(AssertionError('No queue')))
    app.config.update(DIRECT_DELIVERY=True,LIFECYCLE_HTTP=lambda *a,**k:sent.append(a) or {})
    response=register(app.test_client())
    assert response.status_code==200 and len(sent)==2
    with app.app_context():
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='support_account_created'))
        assert event.status=='accepted'
        app.extensions['notifications']['process'](event.id)
    assert len(sent)==2


def test_lifecycle_provider_failure_preserves_account_and_failure_record(app,monkeypatch):
    for key,value in {'SENDGRID_API_KEY':'unit','SENDGRID_FROM_EMAIL':'notices@example.com'}.items():monkeypatch.setenv(key,value)
    app.config.update(DIRECT_DELIVERY=True,LIFECYCLE_HTTP=lambda *a,**k:(_ for _ in ()).throw(TimeoutError()))
    response=register(app.test_client())
    assert response.status_code==200
    with app.app_context():
        assert db.session.scalar(select(Student))
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='support_account_created'))
        assert event.status=='failed' and event.attempts==1


def test_security_code_is_sent_before_other_account_notifications(app,monkeypatch):
    from test_security import prepare_immediate_email
    student=prepare_immediate_email(app);order=[]
    app.config.update(DIRECT_DELIVERY=True,AUTH_HTTP=lambda *a,**k:order.append('verification') or {},LIFECYCLE_HTTP=lambda *a,**k:order.append('notification') or {})
    monkeypatch.setattr('integration_jobs.publish',lambda *a:(_ for _ in ()).throw(AssertionError('No queue')))
    register(student)
    assert order[0]=='verification' and 'notification' in order


def test_rolled_back_event_is_never_sent(app):
    app.config['DIRECT_DELIVERY']=True;sent=[]
    app.config['LIFECYCLE_HTTP']=lambda *a,**k:sent.append(a) or {}
    @app.get('/test/rolled-back-event')
    def rollback_event():
        student=Student(id='rollback-test',name='Test',email='rollback@example.com',phone='+14375550123',country='Canada',kind='student',password_hash='unused')
        db.session.add(student);db.session.flush();db.session.rollback()
        return 'ok'
    assert app.test_client().get('/test/rolled-back-event').status_code==200 and not sent
