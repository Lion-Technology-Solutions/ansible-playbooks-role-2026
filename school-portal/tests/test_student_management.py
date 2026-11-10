import json
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select,func
from app import db,Student,Enrollment,StudentControl,StudentArchive,TuitionControl,StudentManagementAudit,PaymentEntry,LifecycleEvent,BillingSelection,TuitionWhatsAppPreference,now
from test_global_portal import setup,schedule
from test_portal import csrf,register,admin,analytics,approve


def manage(manager,sid,**fields):
    path='/admin/students/'+sid+'/manage'
    return manager.post(path,data={'csrf':csrf(manager,'/admin?view=students'),'admin_password':'test-admin-password',**fields},follow_redirects=True)


def ids(app):
    with app.app_context():return db.session.scalar(select(Student.id))


def set_tuition(manager,sid,eid,status,**fields):
    return manage(manager,sid,action='tuition',enrollment_id=eid,tuition_status=status,records_confirmed='yes',reason='Verified historical tuition record.',**fields)


def test_dashboard_existing_student_reconciliation_grants_without_fake_cash(app):
    student,manager,cid,cohort,eid,*_=setup(app);sid=ids(app)
    page=manager.get('/admin').data
    for label in (b'Tuition status',b'Manage student',b'Send email',b'Open WhatsApp'):assert label in page
    response=manage(manager,sid,action='approve_existing',enrollment_id=eid,records_confirmed='yes',reason='Existing student paid outside portal, reviewed records.')
    assert response.status_code==200
    with app.app_context():
        e=db.session.get(Enrollment,eid)
        assert e.status=='approved' and e.payment_status=='verified'
        assert db.session.get(TuitionControl,eid).status=='current'
        assert not db.session.scalar(select(PaymentEntry))
        assert db.session.scalar(select(StudentManagementAudit)).action=='tuition_current'
    assert b'Class access locked' not in student.get('/portal').data
    set_tuition(manager,sid,eid,'completed')
    with app.app_context():assert db.session.get(TuitionControl,eid).status=='completed'
    assert b'Completed' in manager.get('/admin').data
    assert manager.get('/admin/students/'+sid+'/review').status_code==200


def test_overdue_revokes_all_learning_and_current_restores_without_erasing_manual_hold(app):
    student,manager,cid,cohort,eid,*_=setup(app);sid=ids(app)
    set_tuition(manager,sid,eid,'current');schedule(manager,cid,cohort,starts_at=(now()-timedelta(minutes=1)).replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M'))
    from app import ClassSession
    with app.app_context():lid=db.session.scalar(select(ClassSession.id))
    assert student.get(f'/class/{lid}/join').status_code==302
    set_tuition(manager,sid,eid,'overdue')
    assert student.get(f'/class/{lid}/join').status_code==403
    assert student.get(f'/course/{cid}/materials').status_code==403
    with app.app_context():
        assert db.session.get(Enrollment,eid).status=='revoked'
        rows=list(db.session.scalars(select(LifecycleEvent).where(LifecycleEvent.kind=='tuition_status',LifecycleEvent.payload['status'].as_string()=='overdue')))
        assert {row.channel for row in rows}=={'email','whatsapp'}
        # An older approval endpoint cannot accidentally override an overdue hold.
        db.session.get(Enrollment,eid).status='approved';db.session.commit()
        assert db.session.get(Enrollment,eid).status=='revoked'
    set_tuition(manager,sid,eid,'current')
    assert student.get(f'/class/{lid}/join').status_code==302
    manage(manager,sid,action='revoke',enrollment_id=eid,reason='Manual review')
    with app.app_context():assert db.session.get(TuitionControl,eid).access_hold
    assert student.get(f'/class/{lid}/join').status_code==403
    set_tuition(manager,sid,eid,'completed')
    assert student.get(f'/class/{lid}/join').status_code==302


def test_suspend_delete_restore_and_reauthentication(app):
    student,manager,cid,cohort,eid,*_=setup(app);sid=ids(app)
    assert manage(manager,sid,action='suspend',admin_password='incorrect').status_code==403
    assert student.get('/portal').status_code==200
    assert manage(manager,sid,action='delete',confirm_number='wrong').status_code==400
    with app.app_context():number=db.session.get(Student,sid).student_number
    assert manage(manager,sid,action='delete',confirm_number=number).status_code==200
    assert student.get('/portal').status_code==302
    with app.app_context():assert db.session.get(StudentArchive,sid) and db.session.get(Student,sid)
    assert number.encode() not in manager.get('/admin?view=students').data
    assert number.encode() in manager.get('/admin?view=students&archived=yes').data
    assert manage(manager,sid,action='restore').status_code==200
    with app.app_context():assert not db.session.get(StudentArchive,sid) and not db.session.get(StudentControl,sid).active
    manage(manager,sid,action='reactivate')
    with app.app_context():assert db.session.get(StudentControl,sid).active
    assert not app.test_client().post('/admin/students/'+sid+'/manage',data={'action':'suspend'}).status_code==200


def test_email_and_student_whatsapp_status_use_correct_template_and_skip_stale_notices(app,monkeypatch):
    student,manager,cid,cohort,eid,*_=setup(app);sid=ids(app)
    set_tuition(manager,sid,eid,'current')
    with app.app_context():
        row=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='tuition_status',LifecycleEvent.channel=='whatsapp'));identity=row.id
        app.extensions['notifications']['process'](identity)
        assert row.status=='awaiting_student_consent'
        db.session.add(TuitionWhatsAppPreference(student_id=sid,phone='+14375550123',enabled=True,consented_at=now()));db.session.commit()
    for k,v in {'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'unit','TWILIO_WHATSAPP_FROM':'whatsapp:+19152219465','TWILIO_TUITION_TEMPLATE_SID':'HX'+'c'*32,'SENDGRID_API_KEY':'unit','SENDGRID_FROM_EMAIL':'mail@example.com'}.items():monkeypatch.setenv(k,v)
    sent=[];app.config['LIFECYCLE_HTTP']=lambda *args:(sent.append(args) or {'sid':'SMunit'})
    with app.test_request_context('/'):
        app.extensions['notifications']['process'](identity);app.extensions['notifications']['process'](identity)
    assert len(sent)==1 and sent[0][2]['To']=='whatsapp:+14375550123' and sent[0][2]['ContentSid']=='HX'+'c'*32
    variables=json.loads(sent[0][2]['ContentVariables']);assert variables['2']=='current' and variables['4'].endswith('/payments/enrollment/'+str(eid))
    assert manage(manager,sid,action='email',subject='Your tuition record',body='Your existing enrollment was reviewed.').status_code==200
    with app.test_request_context('/'):
        email=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='admin_message'))
        app.extensions['notifications']['process'](email.id)
        assert sent[-1][2]['subject']=='Your tuition record'
        assert sent[-1][2]['personalizations'][0]['to'][0]['email']==db.session.get(Student,sid).email
    set_tuition(manager,sid,eid,'overdue');set_tuition(manager,sid,eid,'completed')
    with app.app_context():
        old=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='tuition_status',LifecycleEvent.channel=='whatsapp',LifecycleEvent.payload['status'].as_string()=='overdue'))
        app.extensions['notifications']['process'](old.id);assert old.status=='cancelled'


def test_daily_plan_reconciliation_revokes_overdue_then_restores_when_caught_up(app):
    student,manager,cid,cohort,eid,*_=setup(app);sid=ids(app)
    set_tuition(manager,sid,eid,'current')
    with app.app_context():
        db.session.add(BillingSelection(enrollment_id=eid,plan='installments',currency='USD',total=Decimal('300'),installments=3,starts_on=now().date()-timedelta(days=2),checkout_url=''));db.session.commit()
        assert app.extensions['student_management']['sweep']()==1
        assert db.session.get(TuitionControl,eid).status=='overdue' and db.session.get(Enrollment,eid).status=='revoked'
        assert app.extensions['student_management']['sweep']()==0
        db.session.add(PaymentEntry(enrollment_id=eid,reference='TUITION-FIRST',amount=100,currency='USD'));db.session.commit()
        assert app.extensions['student_management']['sweep']()==1
        assert db.session.get(TuitionControl,eid).status=='current' and db.session.get(Enrollment,eid).status=='approved'
    manage(manager,sid,action='revoke',enrollment_id=eid)
    with app.app_context():
        db.session.add(PaymentEntry(enrollment_id=eid,reference='TUITION-REST',amount=200,currency='USD'));db.session.commit()
        app.extensions['student_management']['sweep']()
        assert db.session.get(Enrollment,eid).status=='revoked'
    set_tuition(manager,sid,eid,'completed')
    with app.app_context():assert db.session.get(Enrollment,eid).status=='approved'


def test_stale_tuition_notice_and_reminder_cancel_after_manual_hold_or_completion(app):
    student,manager,cid,cohort,eid,*_=setup(app);sid=ids(app)
    set_tuition(manager,sid,eid,'current')
    with app.app_context():old=list(db.session.scalars(select(LifecycleEvent.id).where(LifecycleEvent.kind=='tuition_status')))
    manage(manager,sid,action='revoke',enrollment_id=eid)
    with app.app_context():
        for identity in old:
            app.extensions['notifications']['process'](identity)
            assert db.session.get(LifecycleEvent,identity).status=='cancelled'
        db.session.add(BillingSelection(enrollment_id=eid,plan='installments',currency='USD',total=300,installments=3,starts_on=now().date(),checkout_url=''));db.session.commit()
        reminder=app.extensions['notifications']['enqueue'](db.session.get(Student,sid),'payment_due',payload={'enrollment_id':eid,'amount':'100 USD'})
        db.session.commit();identity=reminder.id
    set_tuition(manager,sid,eid,'completed')
    with app.app_context():
        app.extensions['notifications']['process'](identity)
        assert db.session.get(LifecycleEvent,identity).status=='cancelled'


def test_reconciliation_requires_attestation_and_rejects_cross_student_enrollment(app):
    student,manager,cid,cohort,eid,*_=setup(app);sid=ids(app)
    assert manage(manager,sid,action='tuition',enrollment_id=eid,tuition_status='completed',reason='Review').status_code==400
    other=app.test_client();register(other,'unrelated@example.com')
    with app.app_context():other_id=db.session.scalar(select(Student.id).where(Student.id!=sid))
    assert manage(manager,other_id,action='tuition',enrollment_id=eid,tuition_status='current',reason='Review',records_confirmed='yes').status_code==403
    with app.app_context():assert db.session.get(Enrollment,eid).status!='approved'
