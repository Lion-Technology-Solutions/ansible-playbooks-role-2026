"""Country routing, closed manual paths and provider-only checkout."""
import pytest
from sqlalchemy import select
from app import db,Enrollment,LocalCheckout,LocalPaymentQuote,ManualFeePayment,PlanPaymentProof
from payment_routing import AFRICA_CODES,provider_for_country
from test_global_portal import setup
from test_link_payment_plan import setup_plan
from test_portal import csrf


@pytest.mark.parametrize('country',['Cameroon','Nigeria','Ghana','Kenya','South Africa','Morocco','Egypt','Algeria','Tunisia','Angola','Somalia','Zimbabwe','South Sudan','Western Sahara','Réunion','Mayotte'])
def test_africa_uses_flutterwave(country):
    assert provider_for_country(country)=='flutterwave'


@pytest.mark.parametrize('country',['Canada','United States','France','Spain','India','Brazil','Japan','Australia','United Arab Emirates'])
def test_other_countries_use_stripe(country):
    assert provider_for_country(country)=='stripe'


def test_unknown_country_does_not_default_to_stripe():
    assert provider_for_country(None) is None
    assert provider_for_country('Other') is None
    assert provider_for_country('Invented') is None
    assert len(AFRICA_CODES)==60


@pytest.mark.parametrize('country,provider,wrong',[('Nigeria','flutterwave','stripe'),('Canada','stripe','flutterwave')])
def test_checkout_shows_one_provider_and_rejects_crafted_other_provider(app,country,provider,wrong):
    client,_,_,_,eid,invoice,_=setup(app,country)
    page=client.get(f'/payments/enrollment/{eid}').data
    assert ('data-payment-provider="'+provider+'"').encode() in page
    assert ('data-payment-provider="'+wrong+'"').encode() not in page
    for old in [b'Interac e-Transfer',b'Bank transfer / deposit',b'Submit local payment proof',b'data-method="mtn"',b'Visa cards']:
        assert old not in page
    with app.app_context():qid=db.session.scalar(select(LocalPaymentQuote.id))
    if qid:
        response=client.post(f'/payments/local/{qid}/{wrong}',data={'csrf':csrf(client,'/portal'),'country':'France'})
        assert response.status_code==409
    response=client.post(f'/payments/{invoice}/'+('checkout' if wrong=='stripe' else 'flutterwave'),data={'csrf':csrf(client,'/portal')})
    assert response.status_code==409
    with app.app_context():assert not db.session.scalar(select(LocalCheckout.id))


def test_new_manual_submissions_are_closed_without_deleting_history(app):
    student,_,_,_,eid,iid,_=setup(app)
    for path in [f'/payments/{iid}/manual',f'/enrollment/{eid}/payment']:
        assert student.post(path,data={'csrf':csrf(student,'/portal'),'method':'Cash'}).status_code==410
    with app.app_context():assert not db.session.scalar(select(ManualFeePayment.id))


def test_plan_checkout_has_no_manual_uploads(app):
    student,_,eid=setup_plan(app)
    page=student.get(f'/payments/plans/{eid}').data
    assert b'data-payment-provider="stripe"' in page
    assert b'name="receipt"' not in page and b'local transfer' not in page
    assert student.post(f'/payments/plans/{eid}',data={'csrf':csrf(student,'/portal')}).status_code==410
    with app.app_context():assert not db.session.scalar(select(PlanPaymentProof.id))


def test_pending_attempt_survives_quote_expiry_and_blocks_second_charge(app):
    from datetime import timedelta
    from app import now
    client,_,_,_,eid,_,_=setup(app)
    with app.app_context():
        row=db.session.scalar(select(LocalPaymentQuote));qid=row.id
        db.session.add(LocalCheckout(quote_id=qid,provider='stripe',provider_id='cs_pending',checkout_url='https://checkout.stripe.com/c/pay/pending'))
        row.expires_at=now()-timedelta(minutes=1);db.session.commit()
    body=client.get(f'/payments/enrollment/{eid}').data
    assert b'Checkout needs review' in body
    assert b'Continue to Stripe' not in body
    with app.app_context():assert len(db.session.scalars(select(LocalPaymentQuote)).all())==1


def test_african_unsupported_currency_does_not_fall_back_to_stripe(app):
    client,_,_,_,eid,_,_=setup(app,'Morocco')
    page=client.get(f'/payments/enrollment/{eid}').data
    assert b'data-payment-provider="flutterwave"' in page
    assert b'Checkout temporarily unavailable' in page
    import re
    assert not re.search(rb'action="/payments/local/[^"]+"',page)
    with app.app_context():assert not db.session.scalar(select(LocalCheckout.id))


def test_completed_enrollment_payment_shows_confirmation_instead_of_unavailable(app):
    from app import FeeInvoice
    client,_,_,_,eid,iid,_=setup(app)
    with app.app_context():
        app.extensions['global_experience']['settle_fee'](db.session.get(FeeInvoice,iid),'verified-historical-payment')
        db.session.commit()
    page=client.get(f'/payments/enrollment/{eid}').data
    assert b'>Paid</strong>' in page and b'Go to student dashboard' in page
    import re
    assert not re.search(rb'action="/payments/local/[^"]+"',page)
