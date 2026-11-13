from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
import pytest
from sqlalchemy import select
from app import db,Course,Enrollment,EnrollmentPaymentSchedule,FeeInvoice,BonanzaEnrollment,LocalPaymentQuote,LocalCheckout,PaymentEntry,Student,AuthPolicy
from christmas_bonanza import START,END,ZONE,fourth_saturdays,payment_stages
from test_portal import csrf,register,admin


def campaign(app):
    app.config.update(BONANZA_ENABLED=True,BONANZA_CLOCK=lambda:datetime(2026,11,13,12,tzinfo=timezone.utc))
    client=app.test_client();client.get('/')
    return client


def claim(client,program='devops',level='foundation',day='2027-01-23'):
    return client.post('/christmas-bonanza/claim',data={'csrf':csrf(client,'/christmas-bonanza'),'program':program,'level':level,'date':day,'accept_terms':'yes'})


def enrolled(app,program='devops',level='foundation',country='Canada'):
    client=campaign(app);assert claim(client,program,level).location.endswith('/register')
    result=register(client,country=country)
    assert result.status_code==200
    with app.app_context():eid=db.session.scalar(select(Enrollment.id))
    assert eid
    return client,eid


def test_fourth_saturdays_and_monthly_anniversaries():
    values=fourth_saturdays(date(2026,11,13))
    assert values[:6]==[date(2027,1,23),date(2027,2,27),date(2027,3,27),date(2027,4,24),date(2027,5,22),date(2027,6,26)]
    assert all(d.weekday()==5 and 22<=d.day<=28 for d in values)
    parts=payment_stages(date(2027,1,23),6,date(2026,11,13))
    assert [p['due'] for p in parts]==['2026-11-13','2027-02-23','2027-03-23','2027-04-23','2027-05-23','2027-06-23']
    assert sum(Decimal(p['amount']) for p in parts)==402


def test_public_offer_calendar_curriculum_and_honest_comparison(app):
    client=campaign(app)
    assert b'LIONTECH CHRISTMAS BONANZA' in client.get('/').data
    for program in ('devops','cloud-computing','cybersecurity'):
        page=client.get('/christmas-bonanza?program='+program)
        assert page.status_code==200 and b'2027-01-23' in page.data and b'2027-02-27' in page.data
        assert b'No separate enrollment fee' in page.data and b'data-countdown' in page.data
        assert client.get('/programs/'+program).location.startswith('/christmas-bonanza?')
        assert client.get('/programs/'+program+'/foundation?tab=resources').status_code==403
    page=client.get('/christmas-bonanza?program=cloud-computing&date=2027-01-23&level=foundation&step=review')
    assert page.status_code==200 and b'This monthly offer totals' in page.data
    assert b'not a lower total' in page.data and b'<s>' not in page.data
    assert client.get('/programs/data-analytics').status_code==200
    assert client.get('/christmas-bonanza?program=data-analytics').status_code==404
    assert client.get('/christmas-bonanza?program=devops&date=2027-01-30').status_code==400
    assert client.get('/christmas-bonanza?program=devops&level=invalid').status_code==400
    assert client.get('/christmas-bonanza?program=devops&date=2027-04-24&year=2027&month=1').data.count(b'value="2027-04-24" checked')==1


@pytest.mark.parametrize('program,level,months',[('devops','advanced',6),('cloud-computing','intermediate',3),('cybersecurity','foundation',2)])
def test_signup_snapshots_first_month_without_extra_fee(app,program,level,months):
    client,eid=enrolled(app,program,level)
    with app.app_context():
        offer=db.session.get(BonanzaEnrollment,eid);plan=db.session.get(EnrollmentPaymentSchedule,eid)
        assert offer.program==program and offer.level==level and not offer.confirmed_at
        assert plan.total==67*months and len(plan.stages)==months
        assert plan.stages[0]['amount']=='67.00' and plan.stages[1]['due']=='2027-02-23'
        assert db.session.scalar(select(FeeInvoice).where(FeeInvoice.enrollment_id==eid)).amount_usd==67
        quote=db.session.scalar(select(LocalPaymentQuote).where(LocalPaymentQuote.enrollment_id==eid))
        assert quote.base_amount==67 and quote.amount==Decimal('93.80') and quote.stage==0
        assert db.session.get(Enrollment,eid).status=='pending'
    assert b'No extra enrollment charge' in client.get('/payments/plans/'+str(eid)).data


def test_existing_account_signin_preserves_offer_and_authentication(app):
    client=campaign(app);register(client,country='Canada');client.post('/logout',data={'csrf':csrf(client,'/portal')})
    assert claim(client,'cybersecurity').status_code==302
    response=client.post('/login',data={'csrf':csrf(client,'/login'),'identifier':'student@example.com','password':'student-password'},follow_redirects=True)
    assert response.status_code==200
    with app.app_context():
        offer=db.session.scalar(select(BonanzaEnrollment));assert offer and offer.program=='cybersecurity'
    assert b'No extra enrollment charge' in response.data


def test_forged_choice_and_unaccepted_terms_create_no_enrollment(app):
    client=campaign(app)
    for program,day in [('data-analytics','2027-01-23'),('devops','2027-01-30')]:
        assert claim(client,program,day=day).status_code==302
        with client.session_transaction() as session:assert not session.get('bonanza_choice')
    assert client.post('/christmas-bonanza/claim',data={'csrf':csrf(client,'/christmas-bonanza'),'program':'devops','level':'foundation','date':'2027-01-23'}).status_code==302
    with app.app_context():assert db.session.query(Enrollment).count()==0


def test_existing_paid_enrollment_and_checkout_are_not_repriced(app):
    client,eid=enrolled(app)
    with app.app_context():
        enrollment=db.session.get(Enrollment,eid);enrollment.status='approved';enrollment.payment_status='verified';db.session.get(BonanzaEnrollment,eid).confirmed_at=app.config['BONANZA_CLOCK']();db.session.commit()
        stages=list(db.session.get(EnrollmentPaymentSchedule,eid).stages)
    assert claim(client,day='2027-02-27').status_code==302
    with app.app_context():
        assert db.session.get(BonanzaEnrollment,eid).start_date==date(2027,1,23)
        assert db.session.get(EnrollmentPaymentSchedule,eid).stages==stages
    # A checkout in progress also prevents changing an unconfirmed offer.
    with app.app_context():
        enrollment=db.session.get(Enrollment,eid);enrollment.status='pending';enrollment.payment_status='unpaid';db.session.get(BonanzaEnrollment,eid).confirmed_at=None
        quote=db.session.scalar(select(LocalPaymentQuote).where(LocalPaymentQuote.enrollment_id==eid));db.session.add(LocalCheckout(quote_id=quote.id,provider='stripe',provider_id='cs_pending'));db.session.commit()
    claim(client,day='2027-02-27')
    with app.app_context():assert db.session.get(BonanzaEnrollment,eid).start_date==date(2027,1,23)


@pytest.mark.parametrize('country,provider,amount',[('Canada','stripe',Decimal('93.80')),('Cameroon','flutterwave',Decimal('40200'))])
@pytest.mark.parametrize('sandbox',[False,True])
def test_verified_payment_credits_only_first_month_once_and_sandbox_never_activates(app,country,provider,amount,sandbox):
    client,eid=enrolled(app,country=country)
    with app.app_context():
        quote=db.session.scalar(select(LocalPaymentQuote).where(LocalPaymentQuote.enrollment_id==eid));assert quote.amount==amount
        checkout=LocalCheckout(quote_id=quote.id,provider=provider,provider_id='cs_bonanza' if provider=='stripe' else None,test_mode=sandbox);db.session.add(checkout);db.session.flush()
        service=app.extensions['local_payments'];service['settle'](checkout);db.session.commit()
        assert bool(db.session.get(BonanzaEnrollment,eid).confirmed_at) is (not sandbox)
        assert quote.credited is (not sandbox)
        assert (db.session.get(Enrollment,eid).status=='approved') is (not sandbox)
        if not sandbox:
            assert service['expected'](db.session.get(Enrollment,eid))['amount']==67
            assert service['expected'](db.session.get(Enrollment,eid))['stage']==1
            service['settle'](checkout);db.session.commit()
        assert list(db.session.scalars(select(PaymentEntry.amount)))==([] if sandbox else [Decimal('67.00')])


def test_deadline_uses_toronto_and_paid_plan_continues_after_promotion(app):
    client,eid=enrolled(app)
    app.config['BONANZA_CLOCK']=lambda:END-timedelta(microseconds=1)
    with app.app_context():assert app.extensions['bonanza']['active']()
    app.config['BONANZA_CLOCK']=lambda:END
    with app.app_context():
        assert not app.extensions['bonanza']['active']()
        with pytest.raises(ValueError):app.extensions['local_payments']['expected'](db.session.get(Enrollment,eid))
        db.session.get(BonanzaEnrollment,eid).confirmed_at=END-timedelta(minutes=1);db.session.commit()
        assert app.extensions['local_payments']['expected'](db.session.get(Enrollment,eid))['amount']==67
    assert b'not currently active' in client.get('/christmas-bonanza').data
    assert claim(client,day='2027-04-24').status_code==302


def test_late_callback_requires_explicit_admin_reconciliation(app):
    client,eid=enrolled(app)
    with app.app_context():
        quote=db.session.scalar(select(LocalPaymentQuote).where(LocalPaymentQuote.enrollment_id==eid))
        record=LocalCheckout(quote_id=quote.id,provider='stripe',provider_id='cs_late_bonanza');db.session.add(record);db.session.flush();identity=record.id
        app.config['BONANZA_CLOCK']=lambda:END
        app.extensions['local_payments']['settle'](record);db.session.commit()
        assert record.paid and record.needs_review and not quote.credited
        assert not db.session.get(BonanzaEnrollment,eid).confirmed_at
    manager=app.test_client();admin(manager)
    url='/admin/local-payments/'+identity+'/review'
    data={'csrf':csrf(manager,'/admin'),'funds_verified':'yes','admin_password':'test-admin-password'}
    assert manager.post(url,data=data).status_code==400
    assert b'promo_paid_before_deadline' in manager.get('/admin?view=payments').data
    assert manager.post(url,data=dict(data,promo_paid_before_deadline='yes')).status_code==302
    with app.app_context():
        assert db.session.get(BonanzaEnrollment,eid).confirmed_at
        assert list(db.session.scalars(select(PaymentEntry.amount)))==[Decimal('67.00')]


def test_all_templates_compile(app):
    with app.app_context():
        for template in app.jinja_env.list_templates():app.jinja_env.get_template(template)


def test_offer_signup_still_requires_email_verification(app):
    from cryptography.fernet import Fernet
    from test_security import activate
    client=campaign(app)
    app.config.update(INTEGRATIONS_ENCRYPTION_KEY=Fernet.generate_key().decode(),SECURITY_MAIL_PUBLISH=lambda identity:None)
    with app.app_context():db.session.get(AuthPolicy,1).require_email=True;db.session.commit()
    claim(client)
    page=register(client,country='Canada')
    assert b'Verify your email address' in page.data
    assert client.get('/portal').status_code==302
    with app.app_context():
        eid=db.session.scalar(select(Enrollment.id));assert not db.session.get(BonanzaEnrollment,eid).confirmed_at
    response=activate(app,client)
    assert b'No extra enrollment charge' in response.data
    with app.app_context():assert db.session.get(Enrollment,eid).status=='pending'


@pytest.mark.parametrize('provider,country',[('stripe','Canada'),('flutterwave','Cameroon')])
def test_provider_handoff_and_verified_return_use_67_server_amount(app,monkeypatch,provider,country):
    client,eid=enrolled(app,country=country);actual={};calls=[]
    ready=app.extensions['integrations']['ready'];app.extensions['integrations']['ready']=lambda name:name==provider or ready(name)
    if provider=='stripe':
        def transport(path,payload=None,key=None):
            if path=='account':return {'country':'CA'}
            if path=='country_specs/CA':return {'supported_payment_currencies':['cad']}
            if path=='checkout/sessions':
                calls.append(payload)
                return {'id':'cs_real_bonanza','url':'https://checkout.stripe.com/c/pay/bonanza'}
            if path=='checkout/sessions/cs_real_bonanza':
                with app.app_context():q=db.session.scalar(select(LocalPaymentQuote).where(LocalPaymentQuote.enrollment_id==eid));number=q.enrollment.student.student_number;qid=q.id
                return {'id':'cs_real_bonanza','payment_status':'paid','currency':'cad','amount_total':9380,'client_reference_id':number,'metadata':{'local_quote':qid},'livemode':True}
            raise AssertionError(path)
        app.config['STRIPE_REQUEST']=transport
    else:
        monkeypatch.setenv('FLW_SECRET_KEY','FLWSECK-bonanza-live')
        def transport(url,method='GET',payload=None,headers=None):
            if method=='POST':
                calls.append(payload);actual.update(id=77999,tx_ref=payload['tx_ref'],amount=payload['amount'],currency=payload['currency'],status='successful')
                return {'status':'success','data':{'link':'https://checkout.flutterwave.com/v3/hosted/pay/bonanza'}}
            return {'status':'success','data':dict(actual)}
        app.config['FLUTTERWAVE_HTTP']=transport
    with app.app_context():qid=db.session.scalar(select(LocalPaymentQuote.id).where(LocalPaymentQuote.enrollment_id==eid))
    wrong='flutterwave' if provider=='stripe' else 'stripe'
    assert client.post('/payments/local/'+qid+'/'+wrong,data={'csrf':csrf(client,'/portal')}).status_code!=303
    response=client.post('/payments/local/'+qid+'/'+provider,data={'csrf':csrf(client,'/portal'),'amount':'.01','currency':'USD'})
    assert response.status_code==303
    if provider=='stripe':assert calls[0]['line_items[0][price_data][unit_amount]']==9380
    else:assert Decimal(calls[0]['amount'])==40200 and calls[0]['currency']=='XAF'
    with app.app_context():identity=db.session.scalar(select(LocalCheckout.id))
    for _ in range(2):assert client.get('/payments/local/return/'+identity+('?transaction_id=77999' if provider=='flutterwave' else '')).status_code==302
    with app.app_context():
        assert db.session.get(BonanzaEnrollment,eid).confirmed_at
        assert list(db.session.scalars(select(PaymentEntry.amount)))==[Decimal('67.00')]
        assert db.session.get(Enrollment,eid).status=='approved'


def test_historical_paid_standard_plan_preserved_when_offer_claimed(app):
    client=app.test_client();register(client,country='Canada');client.get('/programs/devops/foundation/enroll')
    from app import Cohort
    with app.app_context():
        course=db.session.scalar(select(Course).where(Course.slug=='devops'))
        cohort=db.session.scalar(select(Cohort).where(Cohort.course_id==course.id).order_by(Cohort.start_date));cohortid=cohort.id
    client.post('/programs/devops/foundation/enroll',data={'csrf':csrf(client,'/portal'),'cohort_id':cohortid,'plan':'installments'},follow_redirects=True)
    with app.app_context():
        enrollment=db.session.scalar(select(Enrollment));eid=enrollment.id;enrollment.status='approved';enrollment.payment_status='verified';db.session.commit()
        old=list(db.session.get(EnrollmentPaymentSchedule,eid).stages)
    app.config.update(BONANZA_ENABLED=True,BONANZA_CLOCK=lambda:datetime(2026,11,13,12,tzinfo=timezone.utc))
    claim(client)
    with app.app_context():
        assert not db.session.get(BonanzaEnrollment,eid)
        assert db.session.get(EnrollmentPaymentSchedule,eid).total==2394
        assert db.session.get(EnrollmentPaymentSchedule,eid).stages==old
