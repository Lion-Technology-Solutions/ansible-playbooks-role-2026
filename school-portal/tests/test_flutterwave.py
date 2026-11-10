import json
from decimal import Decimal
import pytest
from sqlalchemy import select
from app import db,FlutterwaveCheckout,FeeInvoice,PaymentEntry,Enrollment,IntegrationSetting,now
from test_global_portal import setup
from test_portal import csrf,register


def prepare(app,monkeypatch,test=False,currency='USD'):
    monkeypatch.setenv('FLW_SECRET_KEY','FLWSECK_TEST-unit' if test else 'FLWSECK-unit-live')
    monkeypatch.setenv('FLW_WEBHOOK_SECRET','unit-random-webhook-secret')
    monkeypatch.setenv('FLW_CHECKOUT_CURRENCY',currency)
    app.config['FX_PROVIDER']=lambda:({'USD':'1','NGN':'1500'},now())
    state=setup(app)
    if currency=='NGN':
        app.config['FX_PROVIDER']=lambda:({'USD':'1','NGN':'1500'},now())
        from app import FxSnapshot
        with app.app_context():db.session.query(FxSnapshot).delete();db.session.commit()
    calls=[];actual={}
    def transport(url,method='GET',payload=None,headers=None):
        calls.append((url,method,payload))
        if method=='POST':
            actual.update(id=98765,tx_ref=payload['tx_ref'],amount=payload['amount'],currency=payload['currency'],status='successful')
            return {'status':'success','data':{'link':'https://checkout.flutterwave.com/v3/hosted/pay/unit'}}
        return {'status':'success','data':dict(actual)}
    app.config['FLUTTERWAVE_HTTP']=transport
    return state,calls,actual


def checkout(state):
    student,_,_,_,_,iid,number=state
    return student.post('/payments/'+iid+'/flutterwave',data={'csrf':csrf(student,'/portal'),'student_number':number,'amount':'0.01','currency':'JPY'})


def webhook(app,actual,signature='unit-random-webhook-secret',**extra):
    with app.app_context():reference=db.session.scalar(select(FlutterwaveCheckout.reference).order_by(FlutterwaveCheckout.created_at.desc()))
    event={'event':'charge.completed','data':{'id':98765,'tx_ref':reference,'amount':'99999999'}}
    event.update(extra)
    return app.test_client().post('/webhooks/flutterwave',json=event,headers={'verif-hash':signature})


def test_checkout_is_invoice_bound_and_reuses_existing_link(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch)
    response=checkout(state)
    assert response.status_code==303 and response.location=='https://checkout.flutterwave.com/v3/hosted/pay/unit'
    payload=calls[0][2]
    assert payload['amount']=='5.000' and payload['currency']=='USD'
    assert payload['meta']=={'invoice_id':state[5],'student_number':state[6]}
    assert payload['redirect_url']=='https://www.liontechacademy.com/payments/flutterwave/return'
    assert checkout(state).status_code==303 and len(calls)==1
    with app.app_context():assert not db.session.scalar(select(FlutterwaveCheckout)).paid


def test_webhook_authentication_and_verification_are_required(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch);checkout(state)
    assert webhook(app,actual,signature='wrong').status_code==401
    assert app.test_client().post('/webhooks/flutterwave',json={},headers={'flutterwave-signature':'wrong'}).status_code==401
    assert len(calls)==1
    assert webhook(app,actual).status_code==200
    assert webhook(app,actual).status_code==200 and len(calls)==2
    with app.app_context():
        assert db.session.get(FeeInvoice,state[5]).status=='paid'
        assert len(db.session.scalars(select(PaymentEntry)).all())==1
        enrollment=db.session.get(Enrollment,state[4]);assert enrollment.status=='approved' and enrollment.payment_status=='verified'


@pytest.mark.parametrize('field,value',[('status','failed'),('tx_ref','unrelated'),('currency','NGN'),('amount','0.01'),('amount','NaN'),('amount','6.00'),('id',123)])
def test_mismatched_provider_payment_cannot_settle_invoice(app,monkeypatch,field,value):
    state,calls,actual=prepare(app,monkeypatch);checkout(state);actual[field]=value
    response=webhook(app,actual)
    assert response.status_code==400
    with app.app_context():
        assert db.session.get(FeeInvoice,state[5]).status=='pending'
        assert not db.session.scalar(select(PaymentEntry))


def test_return_uses_server_verification_not_redirect_status(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch);checkout(state);actual['status']='failed'
    response=state[0].get('/payments/flutterwave/return',query_string={'status':'successful','tx_ref':actual['tx_ref'],'transaction_id':'98765'},follow_redirects=True)
    assert response.status_code==200 and b'Payment has not been verified' in response.data
    with app.app_context():assert db.session.get(FeeInvoice,state[5]).status=='pending'
    actual['status']='successful'
    response=state[0].get('/payments/flutterwave/return',query_string={'tx_ref':actual['tx_ref'],'transaction_id':'98765'},follow_redirects=True)
    assert b'Flutterwave payment verified' in response.data
    assert webhook(app,actual).status_code==200
    with app.app_context():assert len(db.session.scalars(select(PaymentEntry)).all())==1


def test_sandbox_payments_never_grant_real_access(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch,test=True);checkout(state)
    assert webhook(app,actual).status_code==200
    with app.app_context():
        record=db.session.scalar(select(FlutterwaveCheckout));assert record.paid and record.test_mode and record.needs_review
        assert not db.session.scalar(select(PaymentEntry))
        assert db.session.get(FeeInvoice,state[5]).status=='pending'
        assert db.session.get(Enrollment,state[4]).status!='approved'


def test_checkout_requires_csrf_owner_and_matching_student_number(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch)
    student=state[0];url='/payments/'+state[5]+'/flutterwave'
    assert student.post(url,data={'student_number':state[6]}).status_code==400
    other=app.test_client();register(other,email='other@example.com')
    assert other.post(url,data={'csrf':csrf(other,'/portal'),'student_number':state[6]}).status_code==403
    response=student.post(url,data={'csrf':csrf(student,'/portal'),'student_number':'wrong'},follow_redirects=True)
    assert b'does not match' in response.data and not calls


def test_revoked_enrollment_is_not_restored_by_late_payment(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch);checkout(state)
    with app.app_context():db.session.get(Enrollment,state[4]).status='revoked';db.session.commit()
    assert webhook(app,actual).status_code==200
    with app.app_context():
        assert db.session.get(Enrollment,state[4]).status=='revoked'
        assert db.session.scalar(select(FlutterwaveCheckout)).needs_review


def test_local_currency_is_frozen_and_booked_separately_in_usd(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch,currency='NGN');checkout(state)
    assert calls[0][2]['amount']=='7500.000' and calls[0][2]['currency']=='NGN'
    assert webhook(app,actual).status_code==200
    with app.app_context():assert db.session.scalar(select(PaymentEntry)).amount==Decimal('5.00')


def test_environment_change_and_provider_outage_do_not_confirm_payment(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch);checkout(state)
    monkeypatch.setenv('FLW_SECRET_KEY','FLWSECK_TEST-unit')
    assert webhook(app,actual).status_code==400 and len(calls)==1
    monkeypatch.setenv('FLW_SECRET_KEY','FLWSECK-unit-live')
    app.config['FLUTTERWAVE_HTTP']=lambda *a,**k:(_ for _ in ()).throw(TimeoutError())
    assert webhook(app,actual).status_code==503
    with app.app_context():assert db.session.get(FeeInvoice,state[5]).status=='pending'


def test_checkout_rejects_untrusted_gateway_url_and_settings_show_v3(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch)
    app.config['FLUTTERWAVE_HTTP']=lambda *a,**k:{'status':'success','data':{'link':'https://checkout.flutterwave.com.attacker.example/pay'}}
    response=checkout(state)
    assert response.status_code==302 and 'attacker.example' not in response.location
    page=state[1].get('/admin/integrations/flutterwave')
    assert b'https://www.liontechacademy.com/webhooks/flutterwave' in page.data
    assert b'Switch to V3 webhook' in page.data and b'unit-random-webhook-secret' not in page.data


def test_unrelated_merchant_payments_are_ignored_without_api_call(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch);checkout(state)
    response=webhook(app,actual,data={'tx_ref':'OTHER-MERCHANT-REFERENCE','id':98765})
    assert response.status_code==200 and len(calls)==1


def test_pause_blocks_new_checkout_but_keeps_inflight_verification(app,monkeypatch):
    from cryptography.fernet import Fernet
    state,calls,actual=prepare(app,monkeypatch);checkout(state)
    app.config['INTEGRATIONS_ENCRYPTION_KEY']=Fernet.generate_key().decode()
    with app.app_context():
        service=app.extensions['integrations'];saved=service['config']('flutterwave')
        db.session.add(IntegrationSetting(provider='flutterwave',ciphertext=service['seal']('flutterwave','academy',saved),enabled=False,status='connected'));db.session.commit()
    assert checkout(state).status_code==503
    assert webhook(app,actual).status_code==200
    with app.app_context():assert db.session.get(FeeInvoice,state[5]).status=='paid'


def test_live_checkout_can_follow_sandbox_without_reusing_test_payment(app,monkeypatch):
    state,calls,actual=prepare(app,monkeypatch,test=True);checkout(state)
    assert webhook(app,actual).status_code==200
    monkeypatch.setenv('FLW_SECRET_KEY','FLWSECK-unit-live')
    assert checkout(state).status_code==303
    assert webhook(app,actual).status_code==200
    with app.app_context():
        assert len(db.session.scalars(select(FlutterwaveCheckout)).all())==2
        assert len(db.session.scalars(select(PaymentEntry)).all())==1
        assert db.session.get(FeeInvoice,state[5]).status=='paid'
