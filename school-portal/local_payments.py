"""Immutable local-currency quotes and verified, enrollment-bound checkout."""
import hashlib,json,secrets
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote as escape_url,urlsplit
from types import SimpleNamespace
from flask import g,session,request,abort,redirect,url_for,flash
from sqlalchemy import select
from babel.numbers import get_currency_precision
from providers import ORIGIN,stripe_request,api
from payment_routing import provider_for_country
from flutterwave_payments import CURRENCIES as FLW_CURRENCIES,checkout_url


def install(app,db,m,h):
    Quote,Checkout=m['LocalPaymentQuote'],m['LocalCheckout'];now,aware=m['now'],m['aware'];locale=app.extensions['local_experience'];integrations=app.extensions['integrations']
    def expected(enrollment):
        fixed=app.extensions['program_link_plans']['billing'](enrollment)
        if fixed:
            if not fixed['next']:raise ValueError('This payment plan is complete.')
            part=fixed['next'];return dict(amount=part['amount'],currency=fixed['terms'].currency,stage=part['index'],offset=fixed['collected'],fixed=True)
        invoice=db.session.scalar(select(m['FeeInvoice']).where(m['FeeInvoice'].enrollment_id==enrollment.id))
        if not invoice or invoice.status=='pending':return dict(amount=invoice.amount_usd if invoice else Decimal(5),currency='USD',stage=-1,offset=0,fixed=False)
        terms=db.session.get(m['BillingSelection'],enrollment.id)
        if not terms:raise ValueError('No payment is currently due. Contact support for tuition options.')
        paid=sum(db.session.scalars(select(m['PaymentEntry'].amount).where(m['PaymentEntry'].enrollment_id==enrollment.id,m['PaymentEntry'].currency==terms.currency,~m['PaymentEntry'].id.in_(select(m['FeeLedger'].entry_id)))),Decimal(0))
        from experience import installment_schedule
        cumulative=Decimal(0)
        for index,part in enumerate(installment_schedule(terms.total,terms.installments,terms.starts_on)):
            cumulative+=part['amount']
            if cumulative>paid:return dict(amount=min(part['amount'],cumulative-paid),currency=terms.currency,stage=100+index,offset=paid,fixed=False)
        raise ValueError('This payment plan is complete.')
    def fingerprint(enrollment,value):return hashlib.sha256(json.dumps([enrollment.id,str(value['amount']),value['currency'],value['stage'],str(value['offset'])]).encode()).hexdigest()
    def current(enrollment):
        try:
            value=expected(enrollment);converted=locale['quote'](value['amount'],value['currency'],offset=value['offset'])
            if not converted['available'] or Decimal(converted['amount'])<=0:return None
            key=fingerprint(enrollment,value)
            active=db.session.scalar(select(Quote).join(Checkout,Checkout.quote_id==Quote.id).where(Quote.enrollment_id==enrollment.id,Quote.credited.is_(False)).order_by(Quote.expires_at.desc()).limit(1))
            if active:g.pinned_local_quote=active;return active
            pending=db.session.scalar(select(Quote).join(m['PlanPaymentProof'],m['PlanPaymentProof'].id==Quote.proof_id).where(Quote.enrollment_id==enrollment.id,Quote.fingerprint==key,m['PlanPaymentProof'].status=='submitted').limit(1))
            if pending:g.pinned_local_quote=pending;return pending
            row=db.session.scalar(select(Quote).where(Quote.enrollment_id==enrollment.id,Quote.fingerprint==key,Quote.currency==converted['currency'],Quote.expires_at>now(),Quote.credited.is_(False),Quote.proof_id.is_(None),Quote.manual_id.is_(None)).order_by(Quote.expires_at.desc()).limit(1))
            if not row:
                row=Quote(enrollment_id=enrollment.id,stage=value['stage'],fingerprint=key,base_amount=value['amount'],base_currency=value['currency'],currency=converted['currency'],amount=Decimal(converted['amount']),rate=converted['rate'],as_of=converted['as_of'],expires_at=now()+timedelta(minutes=30));db.session.add(row);db.session.commit()
            g.pinned_local_quote=row
            return row
        except ValueError:return None
    def validate(identity,enrollment,expired=False):
        row=db.session.get(Quote,identity) if identity else None
        if not row or row.enrollment_id!=enrollment.id:raise ValueError('Refresh your payment page to obtain a current local-currency quote.')
        if row.credited or (not expired and (row.proof_id or row.manual_id)) or (not expired and aware(row.expires_at)<=now()) or row.fingerprint!=fingerprint(enrollment,expected(enrollment)):raise ValueError('This payment quote has changed or expired. Refresh before paying.')
        if not expired and row.currency!=locale['state']()['currency']:raise ValueError('Your country changed. Refresh to see a matching payment quote.')
        return row
    def stripe(path,payload=None,key=None):return app.config.get('STRIPE_REQUEST',stripe_request)(path,payload,key) if payload is not None else app.config.get('STRIPE_REQUEST',stripe_request)(path)
    def flutterwave(path,method='GET',payload=None):
        config=integrations['config']('flutterwave',include_disabled=True)
        return app.config.get('FLUTTERWAVE_HTTP',api)('https://api.flutterwave.com/v3/'+path,method,payload,{'Authorization':'Bearer '+config.get('FLW_SECRET_KEY','')})
    def supported(provider,row):
        if row.proof_id or row.manual_id or row.credited or aware(row.expires_at)<=now():return False
        if provider!=provider_for_country(locale['state']()['country']):return False
        cache=getattr(g,'local_provider_support',{});key=(provider,row.currency)
        if key not in cache:cache[key]=check_supported(provider,row);g.local_provider_support=cache
        return cache[key]
    def check_supported(provider,row):
        if not integrations['ready'](provider):return False
        if provider=='flutterwave':return row.currency in FLW_CURRENCIES
        try:
            country=stripe('account').get('country','')
            if not isinstance(country,str) or len(country)!=2 or not country.isalpha():return False
            currencies=stripe('country_specs/'+country.upper()).get('supported_payment_currencies',[])
            return row.currency.lower() in [str(c).lower() for c in currencies] and get_currency_precision(row.currency) in (0,2)
        except Exception:return False
    def legacy_pending(enrollment):
        invoice=db.session.scalar(select(m['FeeInvoice']).where(m['FeeInvoice'].enrollment_id==enrollment.id))
        if not invoice:return False
        return any(db.session.scalar(select(m[name].id).where(m[name].invoice_id==invoice.id,m[name].paid.is_(False)).limit(1)) for name in ('CardCheckout','FlutterwaveCheckout'))
    def minor(row):return int(row.amount*(100 if row.currency in ('ISK','UGX') else 10**get_currency_precision(row.currency)))
    def safe_stripe_url(value):
        parsed=urlsplit(value or '')
        return parsed.scheme=='https' and parsed.hostname=='checkout.stripe.com' and not parsed.username and not parsed.password and parsed.port in (None,443)
    @app.post('/payments/local/<quote_id>/<provider>')
    @h['student_required']()
    def local_checkout(quote_id,provider):
        row=db.get_or_404(Quote,quote_id);enrollment=row.enrollment
        if enrollment.student_id!=g.student.id:abort(403)
        destination=url_for('link_payment_plan',enrollment_id=enrollment.id) if row.stage>=0 and row.stage<100 else url_for('global_payment',enrollment_id=enrollment.id)
        if provider not in ('stripe','flutterwave'):abort(404)
        try:
            # Serialize handoffs for the same enrollment before checking pending attempts.
            db.session.scalar(select(m['Enrollment']).where(m['Enrollment'].id==enrollment.id).with_for_update())
            if legacy_pending(enrollment):raise ValueError('An earlier checkout is awaiting verification. Contact support before paying again.')
            outstanding=db.session.scalar(select(Checkout).join(Quote,Quote.id==Checkout.quote_id).where(Quote.enrollment_id==enrollment.id,Quote.credited.is_(False),Quote.id!=row.id).limit(1))
            if outstanding:raise ValueError('An earlier payment is awaiting verification. Contact support before paying again.')
            validate(row.id,enrollment)
            if not supported(provider,row):raise ValueError('This provider cannot currently charge in your local currency. Please contact support. No alternative provider will be substituted.')
            previous=db.session.scalar(select(Checkout).where(Checkout.quote_id==row.id,Checkout.provider==provider))
            if previous:
                if previous.paid:raise ValueError('Payment is already received. Review its status before paying again.')
                if previous.checkout_url:return redirect(previous.checkout_url,303)
                raise ValueError('This checkout attempt needs support review. Do not pay twice.')
            record=Checkout(quote_id=row.id,provider=provider,test_mode=(integrations['config']('flutterwave').get('FLW_SECRET_KEY','').startswith('FLWSECK_TEST-') if provider=='flutterwave' else False));db.session.add(record);db.session.commit()
            if provider=='stripe':
                payload={'mode':'payment','adaptive_pricing[enabled]':'false','client_reference_id':g.student.student_number,'customer_email':g.student.email,'line_items[0][price_data][currency]':row.currency.lower(),'line_items[0][price_data][unit_amount]':minor(row),'line_items[0][price_data][product_data][name]':enrollment.course.name+' · scheduled payment','line_items[0][quantity]':1,'metadata[local_quote]':row.id,'success_url':ORIGIN+'/payments/local/return/'+record.id,'cancel_url':ORIGIN+destination}
                result=stripe('checkout/sessions',payload,'local-'+record.id)
                if not result.get('id') or not safe_stripe_url(result.get('url')):raise ValueError('Secure checkout is temporarily unavailable. Contact support before retrying.')
                record.provider_id=result['id'];record.checkout_url=result['url']
            else:
                result=flutterwave('payments','POST',{'tx_ref':record.reference,'amount':str(row.amount),'currency':row.currency,'redirect_url':ORIGIN+'/payments/local/return/'+record.id,'customer':{'email':g.student.email,'name':g.student.name},'meta':{'local_quote':row.id,'student_number':g.student.student_number},'customizations':{'title':'LionTech Academy','description':enrollment.course.name+' · scheduled payment'}})
                link=(result.get('data') or {}).get('link')
                if result.get('status')!='success' or not checkout_url(link):raise ValueError('Secure checkout is temporarily unavailable. Contact support before retrying.')
                record.checkout_url=link
            db.session.commit();return redirect(record.checkout_url,303)
        except ValueError as error:db.session.rollback();flash(str(error))
        except Exception:db.session.rollback();app.logger.warning('Local checkout handoff failed.');flash('Local-currency checkout is temporarily unavailable. Please contact support before retrying.')
        return redirect(destination)
    def settle(record,allow_expired=False):
        row=db.session.scalar(select(Quote).where(Quote.id==record.quote_id).with_for_update().execution_options(populate_existing=True))
        enrollment=db.session.scalar(select(m['Enrollment']).where(m['Enrollment'].id==row.enrollment_id).with_for_update().execution_options(populate_existing=True))
        record.paid=True
        if record.test_mode:return
        if row.credited or (not allow_expired and aware(row.expires_at)<now()):record.needs_review=True;return
        try:value=expected(enrollment)
        except ValueError:record.needs_review=True;return
        if row.fingerprint!=fingerprint(enrollment,value):record.needs_review=True;return
        reference='local:'+record.id
        if row.stage==-1:
            invoice=db.session.scalar(select(m['FeeInvoice']).where(m['FeeInvoice'].enrollment_id==enrollment.id).with_for_update())
            if not invoice:record.needs_review=True;return
            app.extensions['global_experience']['settle_fee'](invoice,reference,allow_rejected=False)
        else:
            entry=m['PaymentEntry'](enrollment_id=enrollment.id,reference=reference,amount=row.base_amount,currency=row.base_currency);db.session.add(entry);db.session.flush()
            if row.stage==0:
                invoice=db.session.scalar(select(m['FeeInvoice']).where(m['FeeInvoice'].enrollment_id==enrollment.id).with_for_update());invoice.status='paid';db.session.add(m['FeeLedger'](entry_id=entry.id,invoice_id=invoice.id))
            try:remaining=expected(enrollment)['amount'];complete=False
            except ValueError:complete=True
            control=db.session.get(m['TuitionControl'],enrollment.id)
            grant=enrollment.status!='rejected' and (enrollment.status!='revoked' or bool(control and control.status=='overdue' and not control.access_hold))
            app.extensions['student_management']['change'](enrollment,'completed' if complete else 'current','Verified local-currency provider payment.',source='plan',grant=grant)
        row.credited=True
    def verify(record,transaction_id=None):
        if record.paid:return
        row=record.quote;student=row.enrollment.student
        if record.provider=='stripe':
            actual=stripe('checkout/sessions/'+escape_url(record.provider_id,safe=''))
            if actual.get('id')!=record.provider_id or actual.get('payment_status')!='paid' or actual.get('currency')!=row.currency.lower() or actual.get('amount_total')!=minor(row) or actual.get('client_reference_id')!=student.student_number or actual.get('metadata',{}).get('local_quote')!=row.id:raise ValueError('Payment does not match the secured local-currency quote.')
            record.test_mode=actual.get('livemode') is False
        else:
            if record.test_mode!=integrations['config']('flutterwave',include_disabled=True).get('FLW_SECRET_KEY','').startswith('FLWSECK_TEST-'):raise ValueError('Restore the original checkout environment before verification.')
            if not str(transaction_id or '').isdigit():raise ValueError('Invalid payment reference.')
            result=flutterwave('transactions/'+str(transaction_id)+'/verify');actual=result.get('data') or {}
            if result.get('status')!='success' or str(actual.get('id'))!=str(transaction_id) or actual.get('status')!='successful' or actual.get('tx_ref')!=record.reference or actual.get('currency')!=row.currency or Decimal(str(actual.get('amount','-1')))!=row.amount:raise ValueError('Payment does not match the secured local-currency quote.')
            record.transaction_id='flutterwave:'+str(transaction_id)
        settle(record);db.session.commit()
    @app.get('/payments/local/return/<checkout_id>')
    @h['student_required']()
    def local_checkout_return(checkout_id):
        record=db.get_or_404(Checkout,checkout_id)
        if record.quote.enrollment.student_id!=g.student.id:abort(403)
        try:
            verify(record,request.args.get('transaction_id'));flash('Test payment received. Test transactions do not activate class access.' if record.test_mode else ('Payment received. Administrator review is required before access changes.' if record.needs_review else 'Payment received. Review your enrollment status; account restrictions still apply.'))
        except Exception:db.session.rollback();flash('Payment is awaiting verification. Contact support before paying again.')
        return redirect(url_for('global_payment',enrollment_id=record.quote.enrollment_id))
    def webhook(provider,identity,transaction_id=None):
        column=Checkout.provider_id if provider=='stripe' else Checkout.reference
        record=db.session.scalar(select(Checkout).where(Checkout.provider==provider,column==identity).with_for_update())
        if not record:return None
        try:verify(record,transaction_id)
        except ValueError:db.session.rollback();return ('',400)
        return ('',200)
    @app.post('/admin/local-payments/<checkout_id>/review')
    @h['admin_required']
    def review_local_checkout(checkout_id):
        integrations['reauthenticate']()
        record=db.get_or_404(Checkout,checkout_id)
        if request.form.get('funds_verified')!='yes':abort(400,'Confirm that the provider verified the actual payment.')
        if not record.paid or record.test_mode or not record.needs_review:abort(409,'Only provider-verified live payments awaiting review qualify.')
        if record.quote.credited:abort(409,'This quote was already credited. Reconcile any duplicate payment with the provider.')
        settle(record,allow_expired=True)
        if not record.quote.credited:db.session.rollback();abort(409,'The enrollment balance changed. Reconcile this payment before crediting it.')
        record.needs_review=False
        db.session.add(m['StudentManagementAudit'](student_id=record.quote.enrollment.student_id,enrollment_id=record.quote.enrollment_id,action='local_payment_review',detail='Approved verified payment '+record.id+' against quote '+record.quote_id))
        db.session.commit();flash('Verified local-currency payment credited. Existing account restrictions still apply.')
        return redirect(url_for('admin'))
    def proof_quote(proof):return db.session.scalar(select(Quote).where(Quote.proof_id==proof.id))
    @app.context_processor
    def context():
        return dict(local_checkout_reviews=db.session.scalars(select(Checkout).where(Checkout.paid.is_(True),Checkout.needs_review.is_(True)).order_by(Checkout.id)).all() if session.get('admin') else [],local_provider_test=lambda provider:integrations['config'](provider).get('STRIPE_SECRET_KEY' if provider=='stripe' else 'FLW_SECRET_KEY','').startswith('sk_test_' if provider=='stripe' else 'FLWSECK_TEST-'),legacy_payment_pending=legacy_pending,local_payment_history=lambda enrollment:db.session.scalars(select(Checkout).join(Quote,Quote.id==Checkout.quote_id).where(Quote.enrollment_id==enrollment.id).order_by(Quote.expires_at.desc())).all(),local_payment_quote=current,local_payment_attempt=lambda row:db.session.scalar(select(Checkout).where(Checkout.quote_id==row.id)) if row else None,local_quote_expired=lambda row:aware(row.expires_at)<=now(),local_provider_ready=supported,local_quote_label=lambda row:locale['format'](row.amount,row.currency) if row else 'Local payment unavailable',proof_quote=proof_quote)
    return dict(current=current,validate=validate,expected=expected,webhook=webhook,proof_quote=proof_quote,settle=settle)
