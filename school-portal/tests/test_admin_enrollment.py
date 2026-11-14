from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
import re
import pytest
from sqlalchemy import select
from app import db,Student,Enrollment,EnrollmentPaymentSchedule,PaymentEntry,EnrollmentOpeningCredit,EnrollmentReview,EnrollmentReconciliationAudit,StudentArchive,StudentControl,LifecycleEvent,PaymentDeletionCase,Course,Cohort,TuitionControl,FeeInvoice,now,academy_today
from test_portal import register,admin,csrf,analytics


def setup(app):
    student,manager=app.test_client(),app.test_client();register(student,country='Canada',kind='existing');admin(manager)
    with app.app_context():sid=db.session.scalar(select(Student.id))
    cid,cohort=analytics(app)
    return student,manager,sid,cid,cohort


def review(manager,sid,cid,cohort,eid=None,**values):
    path='/admin/students/'+sid+'/enrollment-review'+('?enrollment_id='+str(eid) if eid else '')
    page=manager.get(path);assert page.status_code==200
    version=re.search(rb'name="version" value="([^"]+)"',page.data)[1].decode()
    fields=dict(csrf=csrf(manager,'/admin'),version=version,course_id=cid,cohort_id=cohort,student_kind='existing',mode='custom',agreed_total='1000',paid_total='400',currency='USD',installment_amount='200',frequency='monthly',next_due=(academy_today()+timedelta(days=20)).isoformat(),confirm_program='yes',confirm_tuition='yes',records_confirmed='yes',allow_access='yes',decision='approve',reason='Verified prior tuition payments and agreed remaining installments.',admin_password='test-admin-password',replace_terms='yes')
    fields.update(values)
    return manager.post(path,data=fields,follow_redirects=True)


def test_admin_assigns_missing_program_and_opening_credit_is_not_new_cash(app):
    student,manager,sid,cid,cohort=setup(app)
    assert b'Assign program &amp; set tuition' in manager.get('/admin?view=students&student='+sid+'&tab=manage').data
    response=review(manager,sid,cid,cohort)
    assert response.status_code==200
    with app.app_context():
        enrollment=db.session.scalar(select(Enrollment));eid=enrollment.id
        assert enrollment.status=='approved' and enrollment.student.kind=='existing'
        assert db.session.get(EnrollmentReview,eid).status=='approved'
        assert db.session.get(EnrollmentOpeningCredit,eid).amount==400
        assert not db.session.scalar(select(PaymentEntry.id))
        state=app.extensions['program_link_plans']['billing'](enrollment)
        assert state['collected']==400 and state['remaining']==600 and state['next']['amount']==200
        assert db.session.get(EnrollmentPaymentSchedule,eid).total==1000
        assert len(list(db.session.scalars(select(EnrollmentReconciliationAudit))))==1
    assert b'600.00' in student.get('/payments/plans/'+str(eid)).data or b'840.00' in student.get('/payments/plans/'+str(eid)).data


@pytest.mark.parametrize('field,value',[('confirm_program',''),('confirm_tuition',''),('student_kind',''),('reason',''),('admin_password','wrong'),('agreed_total','nan'),('paid_total','1001'),('installment_amount','0')])
def test_confirmation_and_financial_validation_are_required(app,field,value):
    _,manager,sid,cid,cohort=setup(app)
    response=review(manager,sid,cid,cohort,**{field:value})
    assert response.status_code in (200,403)
    with app.app_context():assert not db.session.scalar(select(Enrollment.id))


def test_paid_to_date_excludes_portal_payments_already_recorded(app):
    _,manager,sid,cid,cohort=setup(app);review(manager,sid,cid,cohort)
    with app.app_context():
        e=db.session.scalar(select(Enrollment));eid=e.id
        db.session.add(PaymentEntry(enrollment_id=eid,reference='verified-online-100',amount=100,currency='USD'));db.session.commit()
    review(manager,sid,cid,cohort,eid,paid_total='500')
    with app.app_context():
        assert db.session.get(EnrollmentOpeningCredit,eid).amount==400
        state=app.extensions['program_link_plans']['billing'](db.session.get(Enrollment,eid))
        assert state['collected']==500 and state['remaining']==500
        assert len(list(db.session.scalars(select(PaymentEntry))))==1


def test_pending_and_declined_reviews_cannot_be_approved_by_legacy_route(app):
    _,manager,sid,cid,cohort=setup(app);review(manager,sid,cid,cohort,decision='save',allow_access='')
    with app.app_context():eid=db.session.scalar(select(Enrollment.id))
    response=manager.post('/admin',data={'csrf':csrf(manager,'/admin'),'action':'approve','enrollment_id':eid})
    assert '/enrollment-review' in response.location
    review(manager,sid,cid,cohort,eid,decision='decline',reason='Program choice requires correction.')
    with app.app_context():
        e=db.session.get(Enrollment,eid);assert e.status=='rejected'
        assert db.session.get(EnrollmentReview,eid).status=='declined'
        app.extensions['student_management']['change'](e,'current','Old endpoint cannot override a declined review.');db.session.commit()
        assert e.status!='approved'


def test_installment_final_amount_and_invalid_cohort_ownership(app):
    _,manager,sid,cid,cohort=setup(app)
    with app.app_context():other=db.session.scalar(select(Cohort.id).where(Cohort.course_id!=cid))
    review(manager,sid,cid,other)
    with app.app_context():assert not db.session.scalar(select(Enrollment.id))
    review(manager,sid,cid,cohort,agreed_total='1050')
    with app.app_context():
        e=db.session.scalar(select(Enrollment));assert [p['amount'] for p in db.session.get(EnrollmentPaymentSchedule,e.id).stages]==['400.00','200.00','200.00','200.00','50.00']


def overdue(app,manager,sid,cid,cohort):
    review(manager,sid,cid,cohort,paid_total='0',allow_access='',next_due=academy_today().isoformat())
    app.config['PAYMENT_RETENTION_ENABLED']=True
    point=now();app.config['RETENTION_CLOCK']=lambda:point
    with app.app_context():result=app.extensions['account_retention']['sweep']();assert result['warned']==1
    return point


def test_thirty_day_window_daily_targeted_warning_and_recoverable_deletion(app):
    _,manager,sid,cid,cohort=setup(app);point=overdue(app,manager,sid,cid,cohort)
    with app.app_context():
        service=app.extensions['account_retention'];assert service['sweep']()['warned']==0
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='payment_deletion_warning'))
        assert event.student_id==sid and service['valid_warning'](event)
        event.status='accepted';event.accepted_at=point;db.session.commit()
    app.config['RETENTION_CLOCK']=lambda:point+timedelta(days=1)
    with app.app_context():assert app.extensions['account_retention']['sweep']()['warned']==1
    app.config['RETENTION_CLOCK']=lambda:point+timedelta(days=29)
    with app.app_context():
        app.extensions['account_retention']['sweep']();assert not db.session.get(StudentArchive,sid)
    app.config['RETENTION_CLOCK']=lambda:point+timedelta(days=30)
    with app.app_context():
        assert app.extensions['account_retention']['sweep']()['deleted']==1
        assert db.session.get(StudentArchive,sid) and db.session.get(Student,sid)
        assert not db.session.get(StudentControl,sid).active
        assert db.session.scalar(select(EnrollmentPaymentSchedule))
        assert app.extensions['account_retention']['sweep']()['deleted']==0


def test_payment_cancels_warning_and_unconfirmed_delivery_never_deletes(app):
    _,manager,sid,cid,cohort=setup(app);point=overdue(app,manager,sid,cid,cohort)
    app.config['RETENTION_CLOCK']=lambda:point+timedelta(days=40)
    with app.app_context():
        service=app.extensions['account_retention'];service['sweep']();assert not db.session.get(StudentArchive,sid)
        e=db.session.scalar(select(Enrollment));db.session.add(PaymentEntry(enrollment_id=e.id,reference='settled-all',amount=1000,currency='USD'));db.session.commit()
        warning=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='payment_deletion_warning'))
        assert not service['valid_warning'](warning)
        service['sweep']();assert db.session.get(PaymentDeletionCase,sid).status=='resolved'


def test_other_paid_program_protects_account_from_deletion(app):
    _,manager,sid,cid,cohort=setup(app);point=overdue(app,manager,sid,cid,cohort)
    with app.app_context():
        other=db.session.scalar(select(Cohort).where(Cohort.course_id!=cid))
        e=Enrollment(student_id=sid,course_id=other.course_id,cohort_id=other.id,status='approved',payment_status='verified');db.session.add(e);db.session.flush()
        db.session.add(EnrollmentPaymentSchedule(enrollment_id=e.id,total=100,currency='USD',checkout_url='',stages=[dict(label='Paid',amount='100',due=academy_today().isoformat())]));db.session.add(PaymentEntry(enrollment_id=e.id,reference='other-program-paid',amount=100,currency='USD'));db.session.commit()
        assert app.extensions['account_retention']['sweep']()['held']==1
        assert db.session.get(PaymentDeletionCase,sid).status=='held'
    app.config['RETENTION_CLOCK']=lambda:point+timedelta(days=50)
    with app.app_context():app.extensions['account_retention']['sweep']();assert not db.session.get(StudentArchive,sid)


def test_static_assets_and_all_templates_compile(app):
    client=app.test_client()
    assert client.get('/static/enrollment-review.js').status_code==200
    assert client.get('/static/enrollment-review.css').status_code==200
    with app.app_context():
        for name in app.jinja_env.list_templates():app.jinja_env.get_template(name)


def test_future_installment_does_not_start_deletion_and_no_program_is_not_debt(app):
    _,manager,sid,cid,cohort=setup(app);app.config['PAYMENT_RETENTION_ENABLED']=True
    with app.app_context():
        assert app.extensions['account_retention']['sweep']()['warned']==0
        assert not db.session.get(PaymentDeletionCase,sid)
    review(manager,sid,cid,cohort)
    with app.app_context():assert app.extensions['account_retention']['sweep']()['warned']==0


def test_deletion_can_be_paused_globally_and_extending_is_audited(app):
    _,manager,sid,cid,cohort=setup(app);point=overdue(app,manager,sid,cid,cohort)
    url='/admin/payment-deletion/'+sid+'/extend'
    assert manager.post(url,data={'csrf':csrf(manager,'/admin'),'admin_password':'test-admin-password','reason':'Agreed payment extension.'}).status_code==302
    with app.app_context():assert db.session.get(PaymentDeletionCase,sid).deadline_at.date()==(point+timedelta(days=60)).date()
    assert manager.post('/admin/payment-deletion/policy',data={'csrf':csrf(manager,'/admin'),'admin_password':'test-admin-password','reason':'Review imported records.'}).status_code==302
    with app.app_context():
        assert app.extensions['account_retention']['sweep']()['deleted']==0
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='payment_deletion_warning'))
        assert not app.extensions['account_retention']['valid_warning'](event)


def test_daily_warning_delivery_is_personalized_and_stale_warnings_cancel(app,monkeypatch):
    _,manager,sid,cid,cohort=setup(app);overdue(app,manager,sid,cid,cohort)
    monkeypatch.setenv('SENDGRID_API_KEY','unit');monkeypatch.setenv('SENDGRID_FROM_EMAIL','notifications@example.com');sent=[]
    app.config['LIFECYCLE_HTTP']=lambda *args:sent.append(args) or {}
    with app.app_context():
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='payment_deletion_warning'))
        app.extensions['notifications']['process'](event.id)
        assert event.status=='accepted'
        assert sent[0][2]['personalizations'][0]['to'][0]['email']=='student@example.com'
        assert 'recoverable deletion' in sent[0][2]['content'][0]['value']
        assert 'amount currently due $280.00; total remaining $1,400.00' in sent[0][2]['content'][0]['value']


def test_opted_in_payment_reminders_generate_email_whatsapp_sms_and_send_sms(app,monkeypatch):
    from app import PaymentSMSPreference,TuitionWhatsAppPreference
    student,manager,sid,cid,cohort=setup(app);review(manager,sid,cid,cohort,paid_total='0',allow_access='',next_due=academy_today().isoformat())
    response=student.post('/account/payment-alerts',data={'csrf':csrf(student,'/account/payment-alerts'),'phone':'+14375550123','sms':'yes','whatsapp':'yes'})
    assert response.status_code==302
    for key,value in {'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'unit','TWILIO_WHATSAPP_FROM':'whatsapp:+19152219465','TWILIO_PAYMENT_SMS_FROM':'+14375550199','TWILIO_PAYMENT_TEMPLATE_SID':'HX'+'c'*32}.items():monkeypatch.setenv(key,value)
    sent=[];app.config['LIFECYCLE_HTTP']=lambda *args:sent.append(args) or {'sid':'SMverified'}
    with app.app_context():
        app.extensions['notifications']['due_reminders']()
        events=list(db.session.scalars(select(LifecycleEvent).where(LifecycleEvent.kind=='payment_due')))
        assert {e.channel for e in events}=={'email','whatsapp','sms'}
        sms=next(e for e in events if e.channel=='sms');app.extensions['notifications']['process'](sms.id)
        assert sms.status=='accepted' and sent[-1][2]['To']=='+14375550123'
        assert '280.00' in sent[-1][2]['Body'] or '200' in sent[-1][2]['Body']
        wa=next(e for e in events if e.channel=='whatsapp');app.extensions['notifications']['process'](wa.id)
        assert wa.status=='accepted' and sent[-1][2]['ContentSid']=='HX'+'c'*32
    student.post('/account/payment-alerts',data={'csrf':csrf(student,'/account/payment-alerts'),'phone':'+14375550123'})
    with app.app_context():assert not app.extensions['payment_alerts']['channels'](db.session.get(Student,sid))


def test_upcoming_three_day_reminder_uses_actual_remaining_installment(app):
    _,manager,sid,cid,cohort=setup(app);review(manager,sid,cid,cohort,next_due=(academy_today()+timedelta(days=3)).isoformat())
    with app.app_context():
        assert app.extensions['notifications']['due_reminders']()==1
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='payment_due'))
        assert event.payload['advance_notice'] and event.payload['amount']=='200.00 USD'
        app.extensions['notifications']['process'](event.id)
        assert event.status=='awaiting_email'


def test_catching_up_only_due_installment_stops_deletion_without_paying_full_balance(app):
    _,manager,sid,cid,cohort=setup(app);overdue(app,manager,sid,cid,cohort)
    with app.app_context():
        e=db.session.scalar(select(Enrollment));db.session.add(PaymentEntry(enrollment_id=e.id,reference='arrears-cleared',amount=200,currency='USD'));db.session.commit()
        assert app.extensions['account_retention']['sweep']()['deleted']==0
        assert db.session.get(PaymentDeletionCase,sid).status=='resolved'
        assert app.extensions['program_link_plans']['billing'](e)['remaining']==800


def test_linked_staff_or_admin_identity_holds_automatic_deletion(app):
    from app import MemberPerson,ProfileMembership
    _,manager,sid,cid,cohort=setup(app);point=overdue(app,manager,sid,cid,cohort)
    with app.app_context():
        person=MemberPerson(name='Linked person');db.session.add(person);db.session.flush()
        db.session.add_all([ProfileMembership(actor='student:'+sid,person_id=person.id,kind='student'),ProfileMembership(actor='admin:1',person_id=person.id,kind='admin')]);db.session.commit()
        result=app.extensions['account_retention']['sweep']();assert result['held']==1
        assert db.session.get(PaymentDeletionCase,sid).status=='held'


def test_provider_payment_does_not_bypass_pending_admin_decision(app):
    from app import LocalPaymentQuote,LocalCheckout
    student,manager,sid,cid,cohort=setup(app);review(manager,sid,cid,cohort,paid_total='0',allow_access='',decision='save',next_due=academy_today().isoformat())
    with app.app_context():eid=db.session.scalar(select(Enrollment.id))
    student.get('/payments/plans/'+str(eid))
    with app.app_context():
        quote=db.session.scalar(select(LocalPaymentQuote).where(LocalPaymentQuote.enrollment_id==eid))
        checkout=LocalCheckout(quote_id=quote.id,provider='stripe',provider_id='cs_review_guard');db.session.add(checkout);db.session.flush()
        app.extensions['local_payments']['settle'](checkout);db.session.commit()
        assert quote.credited
        assert db.session.get(Enrollment,eid).status!='approved'
        assert db.session.get(EnrollmentReview,eid).status=='pending'


def test_automatic_deletion_restarts_warning_window_after_manual_restore(app):
    _,manager,sid,cid,cohort=setup(app);point=overdue(app,manager,sid,cid,cohort)
    with app.app_context():
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='payment_deletion_warning'));event.status='accepted';event.accepted_at=point;db.session.commit()
    app.config['RETENTION_CLOCK']=lambda:point+timedelta(days=30)
    with app.app_context():
        app.extensions['account_retention']['sweep']();assert db.session.get(StudentArchive,sid)
        db.session.delete(db.session.get(StudentArchive,sid));db.session.get(StudentControl,sid).active=True;db.session.commit()
        result=app.extensions['account_retention']['sweep']();assert result['deleted']==0 and result['warned']==1
        assert not db.session.get(StudentArchive,sid)
        assert db.session.get(PaymentDeletionCase,sid).deadline_at.date()==(point+timedelta(days=60)).date()


def test_warning_uses_french_and_the_students_timezone(app):
    from app import StudentPreference,StudentLocale
    _,manager,sid,cid,cohort=setup(app);overdue(app,manager,sid,cid,cohort)
    with app.app_context():
        pref=db.session.get(StudentPreference,sid);pref.language='fr';pref.timezone='Africa/Douala';db.session.get(StudentLocale,sid).timezone='Africa/Douala';db.session.commit()
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='payment_deletion_warning'))
        title,body=app.extensions['account_retention']['warning_text'](event)
        assert 'suppression' in title and 'montant actuellement dû' in body
        assert 'Africa/Douala' in body


def test_internal_reconciliation_reason_stays_out_of_student_email(app):
    _,manager,sid,cid,cohort=setup(app)
    review(manager,sid,cid,cohort,reason='INTERNAL-AUDIT-ONLY: verified confidential reference.',student_message='Your agreed payment schedule is available in the portal.')
    with app.app_context():
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='enrollment_approved'))
        assert 'INTERNAL-AUDIT-ONLY' not in event.payload['body']
        assert 'Your agreed payment schedule' in event.payload['body']
        assert 'INTERNAL-AUDIT-ONLY' in db.session.scalar(select(EnrollmentReconciliationAudit)).snapshot['reason']


def test_historical_credit_requires_payment_attestation_even_without_access(app):
    _,manager,sid,cid,cohort=setup(app)
    review(manager,sid,cid,cohort,records_confirmed='',allow_access='',decision='save')
    with app.app_context():assert not db.session.scalar(select(EnrollmentOpeningCredit.enrollment_id))
