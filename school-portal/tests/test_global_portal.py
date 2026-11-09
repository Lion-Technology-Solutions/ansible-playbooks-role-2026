import hashlib
import hmac
import io
import json
import re
import time
from datetime import timedelta
from decimal import Decimal
import pytest
from sqlalchemy import select
from app import (db,Student,StudentPreference,Enrollment,FeeInvoice,ManualFeePayment,PaymentEntry,
                 FeeLedger,CardCheckout,GlobalTuition,ClassSession,ScheduledClass,ClassDelivery,Cohort,
                 BillingSelection,FxSnapshot,now,next_start_dates)
from localization import COUNTRIES,residence,converted
from test_portal import admin,analytics,approve,csrf,post,register,request_enrollment
from test_lessons_files import MemoryStorage


def setup(app,country='Canada'):
    app.config['FILE_STORAGE']=MemoryStorage()
    app.config['FX_PROVIDER']=lambda:({'USD':'1','CAD':'1.40','JPY':'153.987','INR':'88.123','BRL':'5.01','CHF':'.80','KES':'130','KWD':'.305'},now())
    student,manager=app.test_client(),app.test_client()
    register(student,country=country);admin(manager)
    course,cohort=analytics(app);request_enrollment(student,course,cohort)
    with app.app_context():eid=db.session.scalar(select(Enrollment.id));number=db.session.scalar(select(Student)).student_number
    page=student.get(f'/payments/enrollment/{eid}')
    assert page.status_code==200
    with app.app_context():iid=db.session.scalar(select(FeeInvoice.id))
    return student,manager,course,cohort,eid,iid,number


def manual(client,iid,number,**extra):
    values={'csrf':csrf(client,'/portal'),'student_number':number,'amount':'7.00','currency':'CAD','method':'Bank transfer','reference':'LOCAL-TRANSFER-1','note':'Payment proof','receipt':(io.BytesIO(b'%PDF-1.7\nReceipt'),'receipt.pdf')}
    values.update(extra)
    return client.post(f'/payments/{iid}/manual',data=values,follow_redirects=True)


def test_worldwide_residences_currency_rounding_and_cache(app):
    client=app.test_client();client.get('/register')
    assert len(COUNTRIES)==250
    assert residence('Bulgaria')['currency']=='EUR'
    assert residence('Zimbabwe')['currency']=='ZWG'
    calls=[]
    def provider():calls.append(1);return {'USD':'1','JPY':'153.987','INR':'88.123','BRL':'5.01','CHF':'.80','KES':'130','KWD':'.305'},now()
    app.config['FX_PROVIDER']=provider
    for country,currency in [('Japan','JPY'),('India','INR'),('Brazil','BRL'),('Switzerland','CHF'),('Kenya','KES'),('Kuwait','KWD')]:
        r=client.get('/localization',query_string={'country':country});assert r.status_code==200 and r.json['currency']==currency and r.json['available']
    assert len(calls)==1
    assert converted(5,'JPY','153.987')==Decimal('770')
    assert converted(5,'KWD','.305')==Decimal('1.525')
    assert client.get('/localization?country=Invented').status_code==400
    page=client.get('/register');assert b'Japan' in page.data and b'Brazil' in page.data and b'US$5.00' in page.data


def test_rate_failure_never_invents_exchange_rate_and_keeps_reference_timestamp(app):
    client=app.test_client()
    r=client.get('/localization?country=Japan');assert r.json['available'] is False and r.json['currency']=='JPY'
    assert '5.00' in r.json['fee']
    with app.app_context():
        row=db.session.get(FxSnapshot,1);row.rates={'JPY':'150'};row.as_of=now()-timedelta(days=3);row.checked_at=now()-timedelta(days=2);db.session.commit()
    app.config['FX_PROVIDER']=lambda:(_ for _ in ()).throw(ValueError('Outage'))
    r=client.get('/localization?country=Japan');assert r.json['stale'] is True and r.json['as_of']


def test_residence_changes_and_legacy_prices_preserved(app):
    student,manager,course,cohort,eid,iid,number=setup(app)
    post(manager,'/admin/pricing/global',course_id=course,total='100.00',installments='2',full_url='',installment_url='')
    post(student,f'/enrollment/{eid}/plan',plan='installments')
    page=post(student,'/account/location',country='Japan',language='fr',timezone='Asia/Tokyo')
    assert page.status_code==200 and b'JPY' in page.data
    with app.app_context():
        s=db.session.scalar(select(Student));assert s.country=='Japan' and s.student_number==number
        assert db.session.get(StudentPreference,s.id).language=='fr'
        assert db.session.get(FeeInvoice,iid).amount_usd==5
        assert db.session.get(BillingSelection,eid).currency=='USD'
    post(manager,'/admin/pricing/global',course_id=course,total='120.00',installments='3',full_url='',installment_url='')
    with app.app_context():assert db.session.get(BillingSelection,eid).total==100
    assert b'USD base' in student.get('/portal').data


def test_manual_receipt_number_is_owned_private_and_approval_books_only_fee(app):
    student,manager,course,cohort,eid,iid,number=setup(app)
    assert b'does not match' in manual(student,iid,'LT-2027-999999').data
    with app.app_context():assert not db.session.scalar(select(ManualFeePayment.id))
    assert b'submitted for manual review' in manual(student,iid,number).data
    with app.app_context():
        payment=db.session.scalar(select(ManualFeePayment));pid=payment.id
        assert db.session.get(Enrollment,eid).payment_status=='unpaid'
    outsider=app.test_client();register(outsider,'outsider@example.com')
    assert outsider.get(f'/payments/enrollment/{eid}').status_code==403
    assert outsider.get(f'/payments/receipts/{pid}').status_code==403
    assert student.get(f'/payments/receipts/{pid}').data.startswith(b'%PDF')
    assert manager.get(f'/payments/receipts/{pid}').status_code==200
    assert b'already submitted' in manual(student,iid,number).data
    assert manager.post(f'/admin/payments/{pid}/review',data={'csrf':csrf(manager,'/admin'),'decision':'approve'}).status_code==400
    for _ in range(2):post(manager,f'/admin/payments/{pid}/review',decision='approve',funds_verified='yes')
    with app.app_context():
        e=db.session.get(Enrollment,eid);assert e.payment_status=='verified' and e.status=='approved'
        assert db.session.get(FeeInvoice,iid).status=='paid'
        entries=list(db.session.scalars(select(PaymentEntry)));assert len(entries)==1 and entries[0].amount==5 and entries[0].currency=='USD'
        assert db.session.get(FeeLedger,entries[0].id).invoice_id==iid
    assert b'Enrollment active' in student.get(f'/payments/enrollment/{eid}').data
    assert b'/webhooks/stripe' not in student.get(f'/payments/enrollment/{eid}').data


def test_fee_does_not_reduce_full_tuition_balance(app):
    student,manager,course,cohort,eid,iid,number=setup(app)
    post(manager,'/admin/pricing/global',course_id=course,total='100.00',installments='2',full_url='',installment_url='')
    post(student,f'/enrollment/{eid}/plan',plan='installments')
    manual(student,iid,number)
    with app.app_context():pid=db.session.scalar(select(ManualFeePayment.id))
    post(manager,f'/admin/payments/{pid}/review',decision='approve',funds_verified='yes')
    page=student.get(f'/apply/payment/{eid}');assert b'Remaining: USD 100.00' in page.data
    page=manager.get('/admin?currency=USD');assert b'5.00' in page.data and b'100.00' in page.data and b'105.00' in page.data


def test_receipt_validation_and_legacy_reference_needs_owned_number(app):
    student,manager,course,cohort,eid,iid,number=setup(app)
    assert b'valid PDF' in manual(student,iid,number,receipt=(io.BytesIO(b'bad'),'fake.pdf')).data
    assert b'Choose a PDF' in manual(student,iid,number,receipt=(io.BytesIO(b'<script>'),'attack.html')).data
    r=student.post(f'/enrollment/{eid}/payment',data={'csrf':csrf(student,'/portal'),'reference':'BYPASS'},follow_redirects=True)
    assert b'does not match your signed-in account' in r.data
    r=student.post('/payments/verify-student',data={'csrf':csrf(student,'/portal'),'student_number':number});assert r.json['verified']
    assert not student.post('/payments/verify-student',data={'csrf':csrf(student,'/portal'),'student_number':'other'}).json['verified']


def test_card_activation_requires_signed_verified_matching_provider_payment(app,monkeypatch):
    student,manager,course,cohort,eid,iid,number=setup(app)
    assert student.post(f'/payments/{iid}/checkout',data={'csrf':csrf(student,'/portal'),'student_number':number}).status_code==503
    monkeypatch.setenv('STRIPE_SECRET_KEY','test_key');monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','test_webhook')
    actual={'id':'cs_test_verified','status':'open','url':'https://checkout.stripe.com/c/pay/test','currency':'usd','amount_total':500,'payment_status':'unpaid','client_reference_id':number,'metadata':{'invoice_id':iid}}
    seen=[]
    def stripe(path,payload=None,idempotency=None):
        if payload:seen.append((payload,idempotency))
        return dict(actual)
    app.config['STRIPE_REQUEST']=stripe
    for _ in range(2):assert student.post(f'/payments/{iid}/checkout',data={'csrf':csrf(student,'/portal'),'student_number':number}).status_code==303
    assert len(seen)==1 and seen[0][0]['line_items[0][price_data][unit_amount]']==500
    with app.app_context():assert db.session.get(Enrollment,eid).status=='pending'
    def event(signature=True,stamp=None):
        raw=json.dumps({'type':'checkout.session.completed','data':{'object':{'id':actual['id']}}}).encode();stamp=stamp or str(int(time.time()))
        sig=hmac.new(b'test_webhook',stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest() if signature else 'forged'
        return app.test_client().post('/webhooks/stripe',data=raw,content_type='application/json',headers={'Stripe-Signature':'t='+stamp+',v1='+sig})
    assert event(False).status_code==400
    assert event(stamp=str(int(time.time())-1000)).status_code==400
    assert event().status_code==400  # signed but unpaid
    actual['payment_status']='paid';actual['amount_total']=1
    assert event().status_code==400
    actual['amount_total']=500;actual['currency']='cad'
    assert event().status_code==400
    actual['currency']='usd';actual['status']='complete'
    for _ in range(2):assert event().status_code==200
    with app.app_context():
        assert db.session.get(Enrollment,eid).status=='approved'
        assert len(list(db.session.scalars(select(PaymentEntry))))==1
        assert db.session.scalar(select(CardCheckout)).paid
    # Browser return parameters alone never perform fulfillment.
    assert student.get(f'/payments/enrollment/{eid}?checkout=returned').status_code==200


def schedule(manager,course,cohort,**extra):
    fields={'csrf':csrf(manager,'/admin'),'course_id':course,'cohort_id':cohort,'title':'SQL live session','instructor':'Prof Prince','starts_at':(now()+timedelta(hours=1)).replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M'),'timezone':'UTC','duration':'120','join_minutes':'15','zoom_mode':'manual','state':'published','join_url':'https://zoom.us/j/private-class','registration_url':''}
    fields.update(extra)
    return manager.post('/admin/live-classes/save',data=fields,follow_redirects=True)


def test_live_class_scope_join_window_drafts_and_private_calendar(app):
    student,manager,course,cohort,eid,iid,number=setup(app)
    approve(app,student,manager,course,cohort)
    assert schedule(manager,course,cohort).status_code==200
    with app.app_context():
        lesson=db.session.scalar(select(ClassSession));lid=lesson.id
        assert db.session.get(ScheduledClass,lid).published
        assert len(list(db.session.scalars(select(ClassDelivery))))==1
    assert b'SQL live session' in student.get('/live-classes').data
    assert student.get(f'/class/{lid}/join').status_code==403  # too early
    calendar=student.get(f'/calendar/classes/{lid}.ics');assert calendar.status_code==200 and b'BEGIN:VCALENDAR' in calendar.data and b'SEQUENCE:1' in calendar.data
    assert b'private-class' not in calendar.data
    stranger=app.test_client();register(stranger,'stranger@example.com')
    assert stranger.get(f'/calendar/classes/{lid}.ics').status_code==403
    schedule(manager,course,cohort,class_id=lid,starts_at=(now()-timedelta(minutes=1)).replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M'))
    assert b'Join Now' in student.get('/live-classes').data
    assert student.get(f'/class/{lid}/join').location=='https://zoom.us/j/private-class'
    assert b'SEQUENCE:2' in student.get(f'/calendar/classes/{lid}.ics').data
    schedule(manager,course,cohort,class_id=lid,state='draft')
    assert b'SQL live session' not in student.get('/live-classes').data
    assert student.get(f'/class/{lid}/join').status_code==403
    assert student.get(f'/calendar/classes/{lid}.ics').status_code==403
    assert student.post(f'/class/{lid}/register',data={'csrf':csrf(student,'/portal')}).status_code==403


def test_zoom_update_same_meeting_and_email_delivery_state(app,monkeypatch):
    student,manager,course,cohort,eid,iid,number=setup(app);approve(app,student,manager,course,cohort)
    assert b'Connect Zoom account' in schedule(manager,course,cohort,zoom_mode='api').data
    for key in ['ZOOM_ACCOUNT_ID','ZOOM_CLIENT_ID','ZOOM_CLIENT_SECRET','ZOOM_HOST_USER']:monkeypatch.setenv(key,'test_only')
    calls=[]
    def zoom(method,path,payload):calls.append((method,path,payload));return {'id':123456,'join_url':'https://zoom.us/j/123456'} if method=='POST' else {}
    app.config['ZOOM_REQUEST']=zoom
    schedule(manager,course,cohort,zoom_mode='api')
    with app.app_context():lid=db.session.scalar(select(ClassSession.id))
    schedule(manager,course,cohort,class_id=lid,zoom_mode='api')
    assert calls[0][0]=='POST' and calls[1][0]=='PATCH' and calls[1][1]=='meetings/123456'
    assert calls[0][2]['settings']['waiting_room'] and not calls[0][2]['settings']['join_before_host']
    for key in ['SENDGRID_API_KEY','SENDGRID_FROM_EMAIL']:monkeypatch.setenv(key,'test_only')
    delivered=[];app.config['CLASS_EMAIL_SENDER']=lambda student,lesson,calendar:delivered.append(calendar)
    post(manager,'/admin/live-classes/deliver')
    assert len(delivered)==1 and 'SEQUENCE:2' in delivered[0]
    post(manager,'/admin/live-classes/deliver');assert len(delivered)==1
    assert manager.get('/admin/live-classes').status_code==200

@pytest.mark.parametrize('country,currency,rate,minor',[('Canada','CAD','1.40',700),('Japan','JPY','153.987',770),('Uganda','UGX','3740',1870000),('Kuwait','KWD','.305',500)])
def test_local_card_presentment_and_safe_unsupported_precision(app,monkeypatch,country,currency,rate,minor):
    student,manager,course,cohort,eid,iid,number=setup(app,country)
    with app.app_context():
        fx=db.session.get(FxSnapshot,1);fx.rates={**fx.rates,currency:rate};db.session.commit()
    monkeypatch.setenv('STRIPE_SECRET_KEY','test_key');monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','test_webhook')
    seen=[]
    def stripe(path,payload=None,idempotency=None):
        if path=='account':return {'country':'CA'}
        if path=='country_specs/CA':return {'supported_payment_currencies':['usd',currency.lower()]}
        seen.append(payload);return {'id':'cs_local','url':'https://checkout.stripe.com/c/pay/local'}
    app.config['STRIPE_REQUEST']=stripe
    result=student.post(f'/payments/{iid}/checkout',data={'csrf':csrf(student,'/portal'),'student_number':number})
    assert result.status_code==303
    expected_currency='usd' if currency=='KWD' else currency.lower()
    assert seen[0]['line_items[0][price_data][currency]']==expected_currency
    assert seen[0]['line_items[0][price_data][unit_amount]']==minor
    with app.app_context():
        record=db.session.scalar(select(CardCheckout));assert record.expected_cents==minor and record.currency==expected_currency.upper()

@pytest.mark.parametrize('already_settled',[False,True])
def test_late_card_payment_keeps_revocation_and_records_duplicate_for_review(app,monkeypatch,already_settled):
    student,manager,course,cohort,eid,iid,number=setup(app)
    with app.app_context():
        db.session.add(CardCheckout(invoice_id=iid,session_id='cs_late',expected_cents=700,currency='CAD',tender_amount=7,fx_rate='1.4'))
        db.session.commit()
    if already_settled:
        manual(student,iid,number)
        with app.app_context():pid=db.session.scalar(select(ManualFeePayment.id))
        post(manager,f'/admin/payments/{pid}/review',decision='approve',funds_verified='yes')
    post(manager,'/admin',action='reject',enrollment_id=eid)
    monkeypatch.setenv('STRIPE_SECRET_KEY','test_key');monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','late_secret')
    app.config['STRIPE_REQUEST']=lambda *args:{'id':'cs_late','payment_status':'paid','currency':'cad','amount_total':700,'client_reference_id':number,'metadata':{'invoice_id':iid}}
    raw=json.dumps({'type':'checkout.session.completed','data':{'object':{'id':'cs_late'}}}).encode();stamp=str(int(time.time()));signature=hmac.new(b'late_secret',stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
    for _ in range(2):
        assert app.test_client().post('/webhooks/stripe',data=raw,headers={'Stripe-Signature':'t='+stamp+',v1='+signature}).status_code==200
    with app.app_context():
        assert db.session.get(Enrollment,eid).status=='rejected'
        assert db.session.scalar(select(CardCheckout)).needs_review
        entries=list(db.session.scalars(select(PaymentEntry)));assert len(entries)==(2 if already_settled else 1)
        assert all(entry.currency=='USD' and entry.amount==5 for entry in entries)
    assert b'Access review required' in student.get(f'/payments/enrollment/{eid}').data
