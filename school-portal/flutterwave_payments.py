"""Invoice-bound Flutterwave V3 hosted checkout and verified settlement."""
import hmac
import re
import secrets
from datetime import timedelta
from decimal import Decimal,InvalidOperation
from urllib.parse import urlsplit
from flask import abort,flash,g,redirect,request,session,url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from localization import converted,formatted
from providers import ORIGIN,api

CURRENCIES={'USD','NGN','GHS','XAF','XOF','KES','UGX','TZS','ZAR','RWF','EGP','GBP','EUR'}


def checkout_url(value):
    if not isinstance(value,str):return False
    try:
        parsed=urlsplit(value)
        return parsed.scheme=='https' and parsed.hostname in ('checkout.flutterwave.com','checkout.flutterwave.com.ng') and parsed.port in (None,443) and not parsed.username and not parsed.password and bool(parsed.path.strip('/'))
    except ValueError:return False


def install(app,db,m,h):
    Checkout,Invoice=m['FlutterwaveCheckout'],m['FeeInvoice']
    service=app.extensions['integrations'];now,aware=m['now'],m['aware']
    def values():return service['config']('flutterwave',include_disabled=True)
    def call(path,method='GET',payload=None):
        return app.config.get('FLUTTERWAVE_HTTP',api)('https://api.flutterwave.com/v3/'+path,method,payload,{'Authorization':'Bearer '+values().get('FLW_SECRET_KEY','')})
    def amount(invoice):
        currency=service['config']('flutterwave').get('FLW_CHECKOUT_CURRENCY','').strip().upper() or 'USD'
        if currency not in CURRENCIES:raise ValueError('The configured Flutterwave checkout currency is not supported by the portal.')
        value=invoice.amount_usd;rate='1';asof=None
        if currency!='USD':
            rates,asof=h['rates']();rate=rates.get(currency)
            if not rate or not asof or aware(asof)<now()-timedelta(hours=48):raise ValueError('A current exchange rate is unavailable. Use another payment method or contact support.')
            try:
                number=Decimal(str(rate))
                if not number.is_finite() or number<=0:raise ValueError()
                value=converted(invoice.amount_usd,currency,number)
                if value<=0 or value>Decimal('999999999999.999'):raise ValueError()
            except (ValueError,InvalidOperation):raise ValueError('The checkout exchange rate is invalid. Contact support.') from None
        return dict(amount=str(value),currency=currency,formatted=formatted(value,currency),rate=str(rate),as_of=asof)
    def verify(record,transaction_id):
        if record.paid:return
        if record.test_mode!=values().get('FLW_SECRET_KEY','').startswith('FLWSECK_TEST-'):raise ValueError('Restore the original checkout environment before verifying this payment.')
        if not re.fullmatch(r'[1-9][0-9]{0,19}',str(transaction_id)):raise ValueError('Invalid payment transaction reference.')
        result=call('transactions/'+str(transaction_id)+'/verify')
        actual=result.get('data') or {}
        try:paid_amount=Decimal(str(actual.get('amount')))
        except (InvalidOperation,TypeError):raise ValueError('The payment amount could not be verified.') from None
        if (result.get('status')!='success' or str(actual.get('id'))!=str(transaction_id)
            or actual.get('status')!='successful' or actual.get('tx_ref')!=record.reference
            or actual.get('currency')!=record.currency or not paid_amount.is_finite() or paid_amount!=record.amount):
            raise ValueError('The provider payment does not match the saved invoice, amount and currency.')
        invoice=db.session.scalar(select(Invoice).where(Invoice.id==record.invoice_id).with_for_update().execution_options(populate_existing=True))
        if not invoice:raise ValueError('The payment invoice is unavailable.')
        record.transaction_id=str(transaction_id);record.paid=True
        record.needs_review=record.test_mode or invoice.status!='pending' or invoice.enrollment.status in ('rejected','revoked')
        if not record.test_mode:h['settle'](invoice,'flutterwave:'+str(transaction_id),allow_rejected=False)
        # Sandbox payments never produce real ledger entries or class access.
        db.session.commit()

    @app.post('/payments/<invoice_id>/flutterwave')
    @h['student_required']()
    def flutterwave_checkout(invoice_id):
        invoice=db.get_or_404(Invoice,invoice_id);h['owner_enrollment'](invoice.enrollment_id)
        if not service['ready']('flutterwave'):abort(503,'Flutterwave checkout is awaiting provider configuration. Contact support or use another payment method.')
        try:
            h['verify_identity']()
            invoice=db.session.scalar(select(Invoice).where(Invoice.id==invoice_id).with_for_update().execution_options(populate_existing=True))
            if invoice.status!='pending':raise ValueError('This invoice no longer requires payment.')
            test_mode=values().get('FLW_SECRET_KEY','').startswith('FLWSECK_TEST-')
            record=db.session.scalar(select(Checkout).where(Checkout.invoice_id==invoice.id,Checkout.test_mode==test_mode))
            if record:
                if record.paid:raise ValueError('This checkout has already been verified. Review your payment record or contact support.')
                if checkout_url(record.checkout_url) and aware(record.created_at)>now()-timedelta(hours=23):return redirect(record.checkout_url,code=303)
                raise ValueError('This checkout attempt requires support review. Use another payment method or contact support; do not pay twice.')
            quote=amount(invoice)
            record=Checkout(invoice_id=invoice.id,reference='LT-'+secrets.token_hex(24),amount=Decimal(quote['amount']),currency=quote['currency'],fx_rate=quote['rate'],fx_as_of=quote['as_of'],test_mode=test_mode)
            db.session.add(record);db.session.commit()
            result=call('payments','POST',{'tx_ref':record.reference,'amount':str(record.amount),'currency':record.currency,
                'redirect_url':ORIGIN+'/payments/flutterwave/return','customer':{'email':g.student.email,'name':g.student.name},
                'meta':{'invoice_id':invoice.id,'student_number':g.student.student_number},
                'customizations':{'title':'LionTech Academy','description':'Enrollment fee · '+invoice.number}})
            link=(result.get('data') or {}).get('link')
            if result.get('status')!='success' or not checkout_url(link):raise ValueError('Flutterwave did not return a valid secure checkout. Contact support before retrying this invoice.')
            record.checkout_url=link;db.session.commit();return redirect(link,code=303)
        except ValueError as error:db.session.rollback();flash(str(error))
        except Exception:db.session.rollback();app.logger.warning('Flutterwave checkout could not be created.');flash('Flutterwave checkout is temporarily unavailable. No payment or access was confirmed. Contact support before retrying.')
        return redirect(url_for('global_payment',enrollment_id=invoice.enrollment_id))

    @app.get('/payments/flutterwave/return')
    @h['student_required']()
    def flutterwave_return():
        reference=request.args.get('tx_ref','')
        if not re.fullmatch(r'LT-[0-9a-f]{48}',reference):abort(400)
        record=db.session.scalar(select(Checkout).where(Checkout.reference==reference).with_for_update())
        if not record:abort(404)
        h['owner_enrollment'](record.invoice.enrollment_id)
        try:
            verify(record,request.args.get('transaction_id',''))
            flash('Test payment verified. Sandbox transactions do not activate enrollment.' if record.test_mode else 'Flutterwave payment verified. Your enrollment record has been updated; existing access restrictions still apply.')
        except ValueError:
            db.session.rollback();flash('Payment has not been verified. Check your payment record or contact support before paying again.')
        except Exception:
            db.session.rollback();app.logger.warning('Flutterwave return verification unavailable.');flash('Payment verification is temporarily unavailable. Your payment notification can still be verified by the provider webhook; do not pay twice.')
        return redirect(url_for('global_payment',enrollment_id=record.invoice.enrollment_id))

    @app.post('/webhooks/flutterwave')
    def flutterwave_webhook():
        request.max_content_length=65536
        config=values();secret=config.get('FLW_WEBHOOK_SECRET','')
        if not secret or not config.get('FLW_SECRET_KEY'):abort(503)
        signature=request.headers.get('verif-hash','')
        if not signature or not hmac.compare_digest(signature.encode(),secret.encode()):abort(401)
        data=request.get_json(silent=True)
        if not isinstance(data,dict):abort(400)
        if data.get('event')!='charge.completed':return '',200
        payment=data.get('data')
        if not isinstance(payment,dict):abort(400)
        reference=payment.get('tx_ref','')
        if not isinstance(reference,str) or not re.fullmatch(r'LT-[0-9a-f]{48}',reference):return '',200
        record=db.session.scalar(select(Checkout).where(Checkout.reference==reference).with_for_update().execution_options(populate_existing=True))
        if not record:return '',200
        try:verify(record,payment.get('id'));return '',200
        except ValueError:db.session.rollback();return '',400
        except IntegrityError:db.session.rollback();return '',409
        except Exception:db.session.rollback();app.logger.warning('Flutterwave webhook verification unavailable; provider may retry.');return '',503

    @app.context_processor
    def flutterwave_context():
        invoice=request.view_args.get('enrollment_id') if request.view_args and request.endpoint=='global_payment' else None
        checkout=None;quote=None;error=None
        if invoice:
            inv=db.session.scalar(select(Invoice).where(Invoice.enrollment_id==invoice))
            if inv:
                checkout=db.session.scalar(select(Checkout).where(Checkout.invoice_id==inv.id,Checkout.test_mode==values().get('FLW_SECRET_KEY','').startswith('FLWSECK_TEST-')))
                if checkout:quote={'amount':str(checkout.amount),'currency':checkout.currency,'formatted':formatted(checkout.amount,checkout.currency)}
                elif service['ready']('flutterwave'):
                    try:quote=amount(inv)
                    except ValueError as exc:error=str(exc)
        return dict(flutterwave_ready=service['ready']('flutterwave'),flutterwave_quote=quote,flutterwave_record=checkout,flutterwave_quote_error=error,
            flutterwave_test_mode=values().get('FLW_SECRET_KEY','').startswith('FLWSECK_TEST-'),
            flutterwave_admin_records=list(db.session.scalars(select(Checkout).order_by(Checkout.created_at.desc()))) if session.get('admin') and request.endpoint=='admin' else [])
    return dict(verify=verify)
