"""Method selection preserves invoice ownership and manual-payment review."""
import pytest
from sqlalchemy import select
from app import db, ManualFeePayment, Enrollment
from test_global_portal import setup, manual
from test_link_payment_plan import setup_plan


@pytest.mark.parametrize('method', ['Bank deposit', 'MTN Mobile Money', 'Orange Money', 'Interac e-Transfer', 'Cash'])
def test_local_receipts_record_method_without_granting_access(app, method):
    student, manager, course, cohort, eid, iid, number = setup(app)
    response = manual(student, iid, number, method=method)
    assert response.status_code == 200
    assert b'Receipt submitted for manual review' in response.data
    with app.app_context():
        receipt = db.session.scalar(select(ManualFeePayment))
        assert receipt.method == method and receipt.status != 'approved'
        assert db.session.get(Enrollment, eid).payment_status == 'unpaid'


def test_chooser_exposes_brands_and_unconnected_card_instructions(app):
    student, _, _, _, eid, iid, number = setup(app)
    page = student.get(f'/payments/enrollment/{eid}').data
    for label in [b'Visa cards', b'Flutterwave', b'Stripe', b'MTN Mobile Money', b'Orange Money', b'Interac e-Transfer', b'Bank transfer / deposit', b'Cash']:
        assert label in page
    assert b'Ask about availability' in page
    assert b'data-payment-method="Cash"' in page
    rejected = manual(student, iid, number, method='Unconfigured gateway')
    assert b'valid payment amount, currency and method' in rejected.data
    with app.app_context():
        assert db.session.scalar(select(ManualFeePayment)) is None


def test_installment_plan_keeps_alternatives_separate_from_flutterwave_receipts(app):
    student, _, eid = setup_plan(app)
    page = student.get(f'/payments/plans/{eid}').data
    assert b'data-method="mtn"' in page
    assert b'This program currently uses the Flutterwave payment link' in page
    assert b'Ask support how to record an alternative payment' in page
    assert b'data-payment-method="Cash"' not in page
    assert b'Flutterwave transaction reference' in page
