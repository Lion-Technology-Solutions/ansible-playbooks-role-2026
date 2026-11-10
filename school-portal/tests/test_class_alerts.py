import json
from datetime import timedelta
from sqlalchemy import select
from app import db,ClassSession,ScheduledClass,ClassWhatsAppPreference,LifecycleEvent,Student,Enrollment,StudentControl,now
from test_global_portal import setup,schedule
from test_portal import approve,csrf,register


def test_class_status_and_secure_join_landing(app):
    student,manager,cid,cohort,eid,*_=setup(app);approve(app,student,manager,cid,cohort)
    schedule(manager,cid,cohort)
    with app.app_context():lid=db.session.scalar(select(ClassSession.id))
    assert b'Join Class' in student.get('/live-classes').data
    assert student.get(f'/classes/{lid}').location.endswith(f'/live-classes#class-{lid}')
    schedule(manager,cid,cohort,class_id=lid,starts_at=(now()-timedelta(minutes=30)).replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M'))
    assert b'Join Now' in student.get('/live-classes').data
    assert b'Join Class' in student.get('/portal').data  # ongoing classes stay on dashboard
    assert student.get(f'/classes/{lid}').location.endswith(f'/class/{lid}/join')
    assert b'View class / Join' in student.get('/portal').data
    with app.app_context():
        db.session.get(ClassSession,lid).joining.url='';db.session.commit()
    page=student.get('/live-classes').data
    assert b'Joining link not posted' in page and b'Class ended' not in page
    schedule(manager,cid,cohort,class_id=lid,starts_at=(now()-timedelta(hours=3)).replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M'))
    assert b'Class ended' in student.get('/live-classes').data
    stranger=app.test_client();register(stranger,'other-class@example.com')
    assert stranger.get(f'/classes/{lid}').status_code==403
    with app.app_context():db.session.get(Enrollment,eid).status='revoked';db.session.commit()
    assert student.get(f'/classes/{lid}').status_code==403


def test_email_cta_local_time_and_calendar_deep_link(app,monkeypatch):
    student,manager,cid,cohort,eid,*_=setup(app);approve(app,student,manager,cid,cohort)
    schedule(manager,cid,cohort)
    monkeypatch.setenv('SENDGRID_API_KEY','unit-email');monkeypatch.setenv('SENDGRID_FROM_EMAIL','classes@example.com')
    sent=[];app.config['CLASS_HTTP']=lambda *args:sent.append(args)
    with app.test_request_context('/'):
        from providers import send_class_email
        lesson=db.session.scalar(select(ClassSession));person=db.session.scalar(select(Student))
        calendar=app.extensions['global_experience']['calendar_text'](lesson,person)
        send_class_email(person,lesson,calendar)
        assert '/classes/'+str(lesson.id) in calendar
    content=sent[0][2]['content'];plain,html=content[0]['value'],content[1]['value']
    assert 'America/Toronto' in plain and 'View class / Join:' in plain
    assert '<a href="https://www.liontechacademy.com/classes/' in html
    assert 'private-class' not in plain+html


def optin(student,enabled=True):
    return student.post('/account/connections',data={'csrf':csrf(student,'/account/connections'),'reminders':'yes','whatsapp':'yes' if enabled else '', 'whatsapp_phone':'+14375550123'},follow_redirects=True)


def test_student_whatsapp_template_consent_dedup_and_revocation(app,monkeypatch):
    student,manager,cid,cohort,eid,*_=setup(app);approve(app,student,manager,cid,cohort)
    assert optin(student).status_code==200
    for k,v in {'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'unit','TWILIO_WHATSAPP_FROM':'whatsapp:+19152219465','TWILIO_TEMPLATE_SID':'HX'+'a'*32,'TWILIO_CLASS_TEMPLATE_SID':'HX'+'b'*32}.items():monkeypatch.setenv(k,v)
    sent=[];app.config['LIFECYCLE_HTTP']=lambda *args:(sent.append(args) or {'sid':'SMunit'})
    schedule(manager,cid,cohort)
    with app.app_context():
        row=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='class_schedule'));identity=row.id;lid=row.payload['class_id']
        for _ in range(2):app.extensions['notifications']['process'](identity)
        assert db.session.get(LifecycleEvent,identity).status=='accepted'
    assert len(sent)==1
    form=sent[0][2];assert form['To']=='whatsapp:+14375550123' and form['ContentSid']=='HX'+'b'*32
    variables=json.loads(form['ContentVariables']);assert variables['4'].endswith('/classes/'+str(lid)) and '/admin/' not in variables['4']
    # The support template must never be substituted when the class template is missing.
    monkeypatch.delenv('TWILIO_CLASS_TEMPLATE_SID')
    schedule(manager,cid,cohort,class_id=lid)
    with app.app_context():
        row=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='class_schedule',LifecycleEvent.id!=identity));pending=row.id
        app.extensions['notifications']['process'](pending)
        assert db.session.get(LifecycleEvent,pending).status=='awaiting_whatsapp_template'
        db.session.get(Enrollment,eid).status='revoked';db.session.commit()
        app.extensions['notifications']['process'](pending)
        assert db.session.get(LifecycleEvent,pending).status=='cancelled'
    assert len(sent)==1
    assert optin(student,False).status_code==200
    with app.app_context():assert not db.session.scalar(select(ClassWhatsAppPreference)).enabled


def test_published_class_requires_join_url(app):
    student,manager,cid,cohort,*_=setup(app)
    assert b'Add a Zoom joining URL' in schedule(manager,cid,cohort,join_url='').data
    with app.app_context():assert not db.session.scalar(select(ClassSession))


def test_email_deep_link_survives_password_signin(app):
    student,manager,cid,cohort,eid,*_=setup(app);approve(app,student,manager,cid,cohort);schedule(manager,cid,cohort)
    with app.app_context():lid=db.session.scalar(select(ClassSession.id));email=db.session.scalar(select(Student.email))
    anonymous=app.test_client()
    assert anonymous.get(f'/classes/{lid}').location.endswith('/student/login')
    response=anonymous.post('/student/login',data={'csrf':csrf(anonymous,'/student/login'),'email':email,'password':'student-password'},follow_redirects=False)
    assert response.location.endswith('/classes/'+str(lid))
