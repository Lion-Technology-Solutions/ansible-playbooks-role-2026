"""LionTech merchant policy; geographic routing is independent of currency support.

Africa codes follow UN M49 (002), including Western Sahara and African territories.
https://unstats.un.org/unsd/methodology/m49/overview/
Unknown residence fails closed. Provider callbacks remain valid for historical orders.
"""
from flask import abort, g, request
from localization import residence
from sqlalchemy import select

AFRICA_CODES=frozenset('DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG MW ML MR MU YT MA MZ NA NE NG RE RW SH ST SN SC SL SO ZA SS SD TZ TG TN UG EH ZM ZW TF IO'.split())


def provider_for_country(country):
    code=residence(country)['code']
    return ('flutterwave' if code in AFRICA_CODES else 'stripe') if code else None


def install(app):
    @app.before_request
    def enforce_payment_policy():
        if request.method!='POST' or not getattr(g,'student',None):return
        # Keep historical receipts and administrator reconciliation available.
        if request.endpoint in ('manual_fee','payment','link_payment_plan'):
            from app import db, Enrollment, FeeInvoice
            args=request.view_args or {}
            enrollment=db.get_or_404(FeeInvoice,args['invoice_id']).enrollment if request.endpoint=='manual_fee' else db.get_or_404(Enrollment,args['enrollment_id'])
            if enrollment.student_id!=g.student.id:abort(403)
            abort(410,'Manual payment submissions are closed. Return to your payment page and continue with your assigned payment provider.')
        provider={'card_checkout':'stripe','flutterwave_checkout':'flutterwave','local_checkout':(request.view_args or {}).get('provider')}.get(request.endpoint)
        if request.endpoint in ('card_checkout','flutterwave_checkout'):
            from app import db, FeeInvoice, LocalPaymentQuote, LocalCheckout
            invoice=db.get_or_404(FeeInvoice,request.view_args['invoice_id'])
            if invoice.enrollment.student_id!=g.student.id:abort(403)
            if db.session.scalar(select(LocalCheckout.id).join(LocalPaymentQuote,LocalPaymentQuote.id==LocalCheckout.quote_id).where(LocalPaymentQuote.enrollment_id==invoice.enrollment_id,LocalPaymentQuote.credited.is_(False)).limit(1)):
                abort(409,'A payment attempt already exists. Return to your payment page before paying again.')
        if provider and provider!=provider_for_country(app.extensions['local_experience']['state']()['country']):
            abort(409,'Your payment provider changed or does not match your country. Refresh your payment page before continuing.')
    @app.context_processor
    def context():
        country=app.extensions['local_experience']['state']()['country']
        return dict(payment_provider=provider_for_country(country),payment_country=country)
