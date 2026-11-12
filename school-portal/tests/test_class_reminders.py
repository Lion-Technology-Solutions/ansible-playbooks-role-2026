from datetime import timedelta
import json
import pytest
from sqlalchemy import select
from app import db,ClassReminderJob,ClassSession,ScheduledClass,LifecycleEvent,Enrollment,StudentIntegrationPreference,now,aware
from test_global_portal import setup,schedule
from test_portal import approve
from test_class_alerts import optin


def ready(app,monkeypatch):
    student,manager,cid,cohort,eid,*_=setup(app);approve(app,student,manager,cid,cohort);optin(student)
    sent=[];published=[]
    for key,value in {'SENDGRID_API_KEY':'unit','SENDGRID_FROM_EMAIL':'notifications@example.com','TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'unit','TWILIO_WHATSAPP_FROM':'whatsapp:+19152219465','TWILIO_CLASS_TEMPLATE_SID':'HX'+'b'*32}.items():monkeypatch.setenv(key,value)
    app.config.update(LIFECYCLE_HTTP=lambda *args:(sent.append(args) or {'sid':'SMtest'}),CLASS_REMINDER_PUBLISH=lambda payload,delay:published.append((payload,delay)))
    assert schedule(manager,cid,cohort).status_code==200
    with app.app_context():
        job=db.session.scalar(select(ClassReminderJob));identity=job.id;starts=aware(db.session.get(ClassSession,job.class_id).starts_at);lid=job.class_id
    return student,manager,eid,identity,starts,lid,sent,published


def test_twenty_minute_delay_worker_sends_both_channels_and_is_idempotent(app,monkeypatch):
    student,manager,eid,identity,starts,lid,sent,published=ready(app,monkeypatch)
    assert len(published)==1 and published[0][0]=={'kind':'class_reminder','id':identity}
    assert abs(published[0][1].total_seconds()-((starts-timedelta(minutes=20))-now()).total_seconds())<15
    with app.app_context():
        assert app.extensions['class_reminders']['arm']()==0
        with pytest.raises(RuntimeError):app.extensions['class_reminders']['fire'](identity)
        db.session.rollback()
        app.config['CLASS_REMINDER_CLOCK']=lambda:starts-timedelta(minutes=20)
        app.extensions['class_reminders']['fire'](identity);app.extensions['class_reminders']['fire'](identity)
        assert db.session.get(ClassReminderJob,identity).status=='completed'
        rows=list(db.session.scalars(select(LifecycleEvent).where(LifecycleEvent.kind=='class_reminder')))
        assert len(rows)==2 and all(row.status=='accepted' for row in rows)
    email=[call for call in sent if 'sendgrid' in call[0]];wa=[call for call in sent if 'twilio' in call[0]]
    assert len(email)==len(wa)==1
    assert email[0][2]['personalizations'][0]['to'][0]['email']=='student@example.com'
    assert 'Your local class time:' in email[0][2]['content'][0]['value']
    assert wa[0][2]['To']=='whatsapp:+14375550123'
    assert json.loads(wa[0][2]['ContentVariables'])['2'].startswith('Class reminder: ')


@pytest.mark.parametrize('change',['unpublish','reschedule','revoke','started','optout'])
def test_reminders_recheck_current_class_enrollment_and_consent(app,monkeypatch,change):
    student,manager,eid,identity,starts,lid,sent,published=ready(app,monkeypatch)
    app.config['CLASS_REMINDER_CLOCK']=lambda:starts-timedelta(minutes=20)
    with app.app_context():
        if change=='unpublish':db.session.get(ScheduledClass,lid).published=False
        elif change=='reschedule':db.session.get(ScheduledClass,lid).revision+=1
        elif change=='revoke':db.session.get(Enrollment,eid).status='revoked'
        elif change=='started':app.config['CLASS_REMINDER_CLOCK']=lambda:starts
        elif change=='optout':
            from app import ClassWhatsAppPreference
            pref=db.session.scalar(select(ClassWhatsAppPreference));pref.enabled=False
            db.session.scalar(select(StudentIntegrationPreference)).reminders=False
        db.session.commit();sent.clear()
        app.extensions['class_reminders']['fire'](identity)
        assert not sent
        assert db.session.get(ClassReminderJob,identity).status in ('cancelled','completed')


def test_partial_delivery_retry_does_not_resend_accepted_email(app,monkeypatch):
    *_,identity,starts,lid,sent,published=ready(app,monkeypatch)
    app.config['CLASS_REMINDER_CLOCK']=lambda:starts-timedelta(minutes=20)
    def broken(*args):
        if 'twilio' in args[0]:raise RuntimeError('Provider offline')
        sent.append(args);return {}
    app.config['LIFECYCLE_HTTP']=broken
    with app.app_context():
        with pytest.raises(RuntimeError):app.extensions['class_reminders']['fire'](identity)
        app.config['LIFECYCLE_HTTP']=lambda *args:(sent.append(args) or {'sid':'SMrecovered'})
        app.extensions['class_reminders']['fire'](identity)
        assert db.session.get(ClassReminderJob,identity).status=='completed'
    assert len([call for call in sent if 'sendgrid' in call[0]])==1
    assert len([call for call in sent if 'twilio' in call[0]])==1


def test_failed_scheduling_is_durable_and_long_horizon_does_not_exceed_retention(app,monkeypatch):
    student,manager,cid,cohort,eid,*_=setup(app);approve(app,student,manager,cid,cohort)
    app.config['CLASS_REMINDER_PUBLISH']=lambda *args:(_ for _ in ()).throw(RuntimeError('offline'))
    schedule(manager,cid,cohort)
    with app.app_context():
        job=db.session.scalar(select(ClassReminderJob));assert job.status=='failed'
        published=[];app.config['CLASS_REMINDER_PUBLISH']=lambda payload,delay:published.append((payload,delay))
        assert app.extensions['class_reminders']['arm']()==1
        assert timedelta(0)<=published[0][1]<timedelta(days=6)


def test_internal_queue_dispatch_validates_identifier(monkeypatch):
    import asyncio
    from integration_jobs import deliver
    from types import SimpleNamespace
    with pytest.raises(ValueError):asyncio.run(deliver(SimpleNamespace(payload={'kind':'class_reminder','id':'not-a-job-id'})))


def test_future_classes_are_armed_within_queue_horizon_and_rollback_does_not_publish(app):
    student,manager,cid,cohort,eid,*_=setup(app)
    published=[];app.config['CLASS_REMINDER_PUBLISH']=lambda payload,delay:published.append((payload,delay))
    far=now()+timedelta(days=20)
    schedule(manager,cid,cohort,starts_at=far.replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M'))
    assert not published
    with app.app_context():
        job=db.session.scalar(select(ClassReminderJob));assert job.status=='pending'
        app.config['CLASS_REMINDER_CLOCK']=lambda:far-timedelta(days=5)
        assert app.extensions['class_reminders']['arm']()==1
        assert timedelta(days=4)<published[0][1]<timedelta(days=6)
        lesson=ClassSession(course_id=cid,title='Rolled back class',starts_at=far);db.session.add(lesson);db.session.flush()
        detail=ScheduledClass(class_id=lesson.id,published=True,revision=1);db.session.add(detail);db.session.flush()
        app.extensions['class_reminders']['plan'](lesson,detail);db.session.rollback()
        app.extensions['class_reminders']['arm']()
        assert db.session.scalar(select(db.func.count()).select_from(ClassReminderJob))==1 and len(published)==1


def test_role_preview_does_not_arm_pending_reminder_jobs(app,monkeypatch):
    student,manager,eid,identity,starts,lid,sent,published=ready(app,monkeypatch)
    with app.app_context():db.session.get(ClassReminderJob,identity).status='pending';db.session.commit()
    with manager.session_transaction() as state:state['role_preview']='student'
    manager.get('/admin')
    assert len(published)==1
