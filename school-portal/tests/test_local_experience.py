from datetime import datetime,timezone,timedelta,date
from decimal import Decimal
import re,io
import pytest
from sqlalchemy import select
from app import db,Course,Enrollment,FeeInvoice,LocalPaymentQuote,LocalCheckout,StudentLocale,Student,StudentPreference,PaymentEntry,TuitionControl
from test_portal import register,csrf,analytics,request_enrollment,admin
from test_lessons_files import MemoryStorage

RATES={'USD':'1','NGN':'1600','CAD':'1.4','XAF':'601.3','EUR':'.9','GBP':'.8','GHS':'15','INR':'84','JPY':'150'}


def localized(app,country='NG',zone='Africa/Lagos'):
    app.config.update(TRUST_GEO_HEADERS=True,FX_PROVIDER=lambda:(RATES,datetime.now(timezone.utc)),FILE_STORAGE=MemoryStorage(),DATA_ANALYTICS_LINK_PLAN=True)
    client=app.test_client();client.environ_base.update(HTTP_X_VERCEL_IP_COUNTRY=country,HTTP_X_VERCEL_IP_TIMEZONE=zone)
    return client


def plan(app,country='Nigeria'):
    client=localized(app);register(client,country=country);cid,cohort=analytics(app);request_enrollment(client,cid,cohort)
    with app.app_context():eid=db.session.scalar(select(Enrollment.id))
    page=client.get('/payments/plans/'+str(eid))
    assert page.status_code==200
    return client,eid,page


def test_first_response_country_currency_no_browser_conversion_required(app):
    client=localized(app)
    for path in ['/','/apply?program=data-analytics','/courses/data-analytics','/register','/terms','/privacy']:
        page=client.get(path,follow_redirects=True)
        assert page.status_code==200
        assert b'USD' not in page.data and b'US$' not in page.data
        if path.startswith('/courses/') or path.startswith('/apply'):assert '₦'.encode() in page.data
    page=client.get('/courses/data-analytics',follow_redirects=True).data
    assert b'95,984' in page and b'59.99' not in page


def test_geo_headers_untrusted_outside_vercel_and_unknown_never_falls_back(app):
    client=localized(app);app.config['TRUST_GEO_HEADERS']=False
    page=client.get('/courses/data-analytics',follow_redirects=True).data
    assert b'Choose your country' in page and b'USD' not in page and b'US$' not in page
    assert b'59.99' not in page


def test_failed_or_stale_fx_hides_prices_and_checkout(app):
    client=localized(app);app.config['FX_PROVIDER']=lambda:(_ for _ in ()).throw(ValueError('down'))
    page=client.get('/courses/data-analytics',follow_redirects=True).data
    assert b'temporarily unavailable' in page and b'USD' not in page and b'59.99' not in page
    register(client,country='Nigeria');cid,cohort=analytics(app);request_enrollment(client,cid,cohort)
    with app.app_context():eid=db.session.scalar(select(Enrollment.id))
    page=client.get('/payments/plans/'+str(eid)).data
    assert b'name="local_quote_id"' not in page and b'flutterwave.com/pay/' not in page


def test_country_correction_and_browser_timezone_persist_in_profile(app):
    client=localized(app);register(client,country='Nigeria')
    result=client.post('/local-preferences',data={'csrf':csrf(client,'/portal'),'country':'Canada','timezone':'America/Vancouver','zone_choice':'manual','next':'/portal'})
    assert result.status_code==302
    with app.app_context():
        student=db.session.scalar(select(Student));locale=db.session.get(StudentLocale,student.id)
        assert locale.country=='Canada' and locale.currency=='CAD' and locale.timezone=='America/Vancouver' and locale.timezone_manual
        assert db.session.get(StudentPreference,student.id).timezone=='America/Vancouver'
    page=client.get('/courses/data-analytics',follow_redirects=True).data
    assert b'83.99' in page and b'USD' not in page
    assert client.post('/local-preferences',data={'csrf':csrf(client,'/portal'),'timezone':'Invalid/Zone'}).status_code==400


def test_rounded_stages_add_to_total_in_zero_decimal_currency(app):
    client=localized(app,'CM','Africa/Douala');register(client,country='Cameroon');cid,cohort=analytics(app);request_enrollment(client,cid,cohort)
    with app.test_request_context('/'):
        from flask import g
        g.student=db.session.scalar(select(Student))
        service=app.extensions['local_experience'];parts=[Decimal(service['quote'](a,offset=o)['amount']) for a,o in [('10',0),('9.99',10),('19.99','19.99'),('19.99','39.98')]]
        assert sum(parts)==Decimal(service['quote']('59.97')['amount'])


def test_quote_and_receipt_are_bound_to_owner_currency_and_exact_payment(app):
    client,eid,page=plan(app)
    with app.app_context():
        quote=db.session.scalar(select(LocalPaymentQuote));qid=quote.id;assert quote.currency=='NGN' and quote.amount==16000 and quote.base_amount==10
        number=quote.enrollment.student.student_number
    assert b'USD' not in page.data and b'flutterwave.com/pay/' not in page.data
    def receipt(amount):return client.post('/payments/plans/'+str(eid),data={'csrf':csrf(client,'/portal'),'local_quote_id':qid,'local_amount':amount,'student_number':number,'reference':'LOCAL-RECEIPT','receipt':(io.BytesIO(b'%PDF-1.7\ntest'),'receipt.pdf')},follow_redirects=True)
    assert receipt('10').status_code==410
    assert receipt('16000').status_code==410
    with app.app_context():
        quote=db.session.get(LocalPaymentQuote,qid);assert not quote.proof_id and not quote.credited
        assert db.session.get(Enrollment,eid).status!='approved'


def test_weekly_occurrences_preserve_wall_clock_across_dst():
    from scheduling_time import weekly_occurrences
    from zoneinfo import ZoneInfo
    values=weekly_occurrences(datetime(2027,3,6,16),'America/Toronto',3)
    assert [d.hour for d in values]==[21,21,20]
    assert [d.astimezone(ZoneInfo('America/Toronto')).hour for d in values]==[16,16,16]
    with pytest.raises(ValueError):weekly_occurrences(datetime(2027,3,7,2,30),'America/Toronto',2)


def test_secure_stripe_uses_server_quote_and_verified_return_is_idempotent(app):
    client,eid,page=plan(app);calls=[]
    client.post('/local-preferences',data={'csrf':csrf(client,'/portal'),'country':'Canada','next':'/portal'})
    client.get('/payments/plans/'+str(eid))
    ready=app.extensions['integrations']['ready'];app.extensions['integrations']['ready']=lambda provider:provider=='stripe' or ready(provider)
    def stripe(path,payload=None,key=None):
        calls.append((path,payload))
        if path=='account':return {'country':'CA'}
        if path=='country_specs/CA':return {'supported_payment_currencies':['ngn','cad']}
        if path=='checkout/sessions':return {'id':'cs_local_test','url':'https://checkout.stripe.com/c/pay/local'}
        if path=='checkout/sessions/cs_local_test':
            with app.app_context():row=db.session.scalar(select(LocalPaymentQuote).where(LocalPaymentQuote.currency=='CAD'));number=row.enrollment.student.student_number;qid=row.id
            return {'id':'cs_local_test','payment_status':'paid','currency':'cad','amount_total':1400,'client_reference_id':number,'metadata':{'local_quote':qid},'livemode':True}
        raise AssertionError(path)
    app.config['STRIPE_REQUEST']=stripe
    with app.app_context():qid=db.session.scalar(select(LocalPaymentQuote.id).where(LocalPaymentQuote.currency=='CAD'))
    response=client.post('/payments/local/'+qid+'/stripe',data={'csrf':csrf(client,'/portal'),'amount':'.01','currency':'USD'})
    assert response.status_code==303
    payload=next(payload for path,payload in calls if path=='checkout/sessions')
    assert payload['adaptive_pricing[enabled]']=='false'
    assert payload['line_items[0][price_data][unit_amount]']==1400 and payload['line_items[0][price_data][currency]']=='cad'
    with app.app_context():checkout_id=db.session.scalar(select(LocalCheckout.id))
    for _ in range(2):assert client.get('/payments/local/return/'+checkout_id).status_code==302
    with app.app_context():
        assert db.session.get(Enrollment,eid).status=='approved'
        assert list(db.session.scalars(select(PaymentEntry.amount)))==[Decimal('10.00')]
        assert db.session.get(LocalPaymentQuote,qid).credited


def test_every_owned_student_payment_view_has_local_prices_and_no_base_labels(app):
    client,eid,page=plan(app)
    for path in ['/portal','/enrollments','/payments/enrollment/'+str(eid),'/payments/plans/'+str(eid),'/apply/payment/'+str(eid),'/account/location']:
        response=client.get(path,follow_redirects=True)
        assert response.status_code==200
        assert b'USD' not in response.data and b'US$' not in response.data
        assert b'flutterwave.com/pay/liontechpaymentplan' not in response.data


def test_quote_expiry_currency_change_and_ownership_block_checkout(app):
    client,eid,page=plan(app)
    with app.app_context():
        row=db.session.scalar(select(LocalPaymentQuote));qid=row.id;row.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);db.session.commit()
    response=client.post('/payments/local/'+qid+'/flutterwave',data={'csrf':csrf(client,'/portal')},follow_redirects=True)
    assert b'changed or expired' in response.data
    other=localized(app);register(other,email='another@example.com',country='Nigeria')
    assert other.post('/payments/local/'+qid+'/flutterwave',data={'csrf':csrf(other,'/portal')}).status_code==403


def test_due_email_uses_recipient_currency_and_local_deadline(app,monkeypatch):
    from app import LifecycleEvent
    from program_link_plans import dated_stages
    client,eid,page=plan(app)
    with app.app_context():
        from app import EnrollmentPaymentSchedule,now
        student=db.session.get(Enrollment,eid).student
        schedule=db.session.get(EnrollmentPaymentSchedule,eid)
        schedule.stages=dated_stages(schedule.stages,date.today()-timedelta(days=7));db.session.commit()
        event=app.extensions['notifications']['enqueue'](student,'payment_due',payload={'enrollment_id':eid,'course':'Data Analytics','amount':'10.00 USD','due':date.today().isoformat()});db.session.commit();identity=event.id
    sent=[]
    ready=app.extensions['integrations']['ready'];app.extensions['integrations']['ready']=lambda provider:provider=='email' or ready(provider)
    monkeypatch.setenv('SENDGRID_API_KEY','test');monkeypatch.setenv('SENDGRID_FROM_EMAIL','test@example.com')
    app.config['LIFECYCLE_HTTP']=lambda *args,**kwargs:sent.append((args,kwargs)) or {}
    with app.app_context():app.extensions['notifications']['process'](identity)
    assert sent
    text=str(sent)
    assert 'USD' not in text and 'Africa/Lagos' in text and ('₦' in text or '\\u20a6' in text)


def test_expired_receipt_can_be_submitted_once_for_manual_review(app):
    client,eid,page=plan(app)
    with app.app_context():
        row=db.session.scalar(select(LocalPaymentQuote));qid=row.id;number=row.enrollment.student.student_number
        row.expires_at=datetime.now(timezone.utc)-timedelta(minutes=1);db.session.commit()
    response=client.post('/payments/plans/'+str(eid),data={'csrf':csrf(client,'/portal'),'local_quote_id':qid,'local_amount':'16000','student_number':number,'reference':'AFTER-EXPIRY','receipt':(io.BytesIO(b'%PDF-1.7\ntest'),'receipt.pdf')},follow_redirects=True)
    assert response.status_code==410
    with app.app_context():
        assert not db.session.get(LocalPaymentQuote,qid).proof_id
        assert not db.session.get(LocalPaymentQuote,qid).credited


def test_admin_can_review_verified_late_payment_without_duplicate_credit(app):
    client,eid,page=plan(app)
    with app.app_context():
        row=db.session.scalar(select(LocalPaymentQuote));row.expires_at=datetime.now(timezone.utc)-timedelta(minutes=1)
        checkout=LocalCheckout(quote_id=row.id,provider='stripe',provider_id='cs_verified_late',paid=True,needs_review=True)
        db.session.add(checkout);db.session.commit();identity=checkout.id
    staff=app.test_client();admin(staff)
    url='/admin/local-payments/'+identity+'/review'
    assert staff.post(url,data={'csrf':csrf(staff,'/admin'),'funds_verified':'yes','admin_password':'wrong'}).status_code==403
    response=staff.post(url,data={'csrf':csrf(staff,'/admin'),'funds_verified':'yes','admin_password':'test-admin-password'})
    assert response.status_code==302
    assert staff.post(url,data={'csrf':csrf(staff,'/admin'),'funds_verified':'yes','admin_password':'test-admin-password'}).status_code==409
    with app.app_context():
        assert list(db.session.scalars(select(PaymentEntry.amount)))==[Decimal('10.00')]
        assert db.session.get(Enrollment,eid).status=='approved'


def test_flutterwave_local_checkout_verifies_exact_tender_before_credit(app,monkeypatch):
    monkeypatch.setenv('FLW_SECRET_KEY','FLWSECK-unit-live')
    monkeypatch.setenv('FLW_WEBHOOK_SECRET','unit-local-secret')
    client,eid,page=plan(app);actual={};calls=[]
    def transport(url,method='GET',payload=None,headers=None):
        calls.append((method,payload))
        if method=='POST':
            actual.update(id=12345,tx_ref=payload['tx_ref'],amount=payload['amount'],currency=payload['currency'],status='successful')
            return {'status':'success','data':{'link':'https://checkout.flutterwave.com/v3/hosted/pay/local'}}
        return {'status':'success','data':dict(actual)}
    app.config['FLUTTERWAVE_HTTP']=transport
    with app.app_context():qid=db.session.scalar(select(LocalPaymentQuote.id))
    response=client.post('/payments/local/'+qid+'/flutterwave',data={'csrf':csrf(client,'/portal'),'amount':'1','currency':'USD'})
    assert response.status_code==303
    assert calls[0][1]['amount']=='16000.000' and calls[0][1]['currency']=='NGN'
    with app.app_context():identity=db.session.scalar(select(LocalCheckout.id))
    actual['amount']='10'
    client.get('/payments/local/return/'+identity+'?transaction_id=12345')
    with app.app_context():assert not db.session.get(LocalPaymentQuote,qid).credited
    actual['amount']='16000'
    for _ in range(2):client.get('/payments/local/return/'+identity+'?transaction_id=12345')
    with app.app_context():
        assert db.session.get(LocalPaymentQuote,qid).credited
        assert list(db.session.scalars(select(PaymentEntry.amount)))==[Decimal('10.00')]


@pytest.mark.parametrize('sandbox',[False,True])
def test_flutterwave_webhook_processes_local_quotes_without_browser_return(app,monkeypatch,sandbox):
    monkeypatch.setenv('FLW_SECRET_KEY','FLWSECK_TEST-unit' if sandbox else 'FLWSECK-unit-live')
    monkeypatch.setenv('FLW_WEBHOOK_SECRET','unit-local-webhook-secret')
    client,eid,page=plan(app);actual={};lookups=[]
    def transport(url,method='GET',payload=None,headers=None):
        if method=='POST':
            actual.update(id=54321,tx_ref=payload['tx_ref'],amount=payload['amount'],currency=payload['currency'],status='successful')
            return {'status':'success','data':{'link':'https://checkout.flutterwave.com/v3/hosted/pay/webhook'}}
        lookups.append(url)
        return {'status':'success','data':dict(actual)}
    app.config['FLUTTERWAVE_HTTP']=transport
    with app.app_context():qid=db.session.scalar(select(LocalPaymentQuote.id))
    assert client.post('/payments/local/'+qid+'/flutterwave',data={'csrf':csrf(client,'/portal')}).status_code==303
    assert actual['tx_ref'].startswith('LTL-')
    event={'event':'charge.completed','data':{'tx_ref':actual['tx_ref'],'id':54321,'amount':'9999999','currency':'USD'}}
    receiver=app.test_client()
    assert receiver.post('/webhooks/flutterwave',json=event,headers={'verif-hash':'wrong'}).status_code==401
    assert not lookups
    actual['amount']='1'
    assert receiver.post('/webhooks/flutterwave',json=event,headers={'verif-hash':'unit-local-webhook-secret'}).status_code==400
    with app.app_context():assert not db.session.get(LocalPaymentQuote,qid).credited
    actual['amount']='16000'
    for _ in range(2):assert receiver.post('/webhooks/flutterwave',json=event,headers={'verif-hash':'unit-local-webhook-secret'}).status_code==200
    with app.app_context():
        row=db.session.scalar(select(LocalCheckout));assert row.paid and row.test_mode==sandbox
        assert db.session.get(LocalPaymentQuote,qid).credited is (not sandbox)
        assert len(list(db.session.scalars(select(PaymentEntry.id))))==(0 if sandbox else 1)
        assert (db.session.get(Enrollment,eid).status=='approved') is (not sandbox)
