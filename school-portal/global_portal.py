"""Global USD pricing, residence, invoice-bound payments and live-class scheduling."""
import io
import json
import os
import secrets
from datetime import timedelta, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from flask import abort, flash, g, jsonify, redirect, render_template, request, Response, session, url_for
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from zoneinfo import ZoneInfo
from babel.numbers import get_currency_precision
from werkzeug.utils import secure_filename
from localization import COUNTRIES, COUNTRY_META, CURRENCIES, TIMEZONES, residence, formatted, converted, fetch_rates
from experience import money_value
from file_storage import validate_image
from providers import ORIGIN, setting, stripe_request, signed_stripe_event, zoom_request, send_class_email


MANUAL_PAYMENT_METHODS=('Bank transfer','Bank deposit','Mobile money','MTN Mobile Money','Orange Money','Interac e-Transfer','Cash','Other')

def install(app,db,m,h):
    M=lambda key:m[key]
    Fx,Invoice,Manual,Checkout=(M(k) for k in ('FxSnapshot','FeeInvoice','ManualFeePayment','CardCheckout'))
    Student,Enrollment,Entry=(M(k) for k in ('Student','Enrollment','PaymentEntry'))
    GlobalTuition,Preference=(M(k) for k in ('GlobalTuition','StudentPreference'))
    Class,Detail,Delivery,Cohort=(M(k) for k in ('ClassSession','ScheduledClass','ClassDelivery','Cohort'))
    now,aware,field=m['now'],m['aware'],m['field']
    student_required,admin_required=h['student_required'],h['admin_required']
    notify,confirm=h['notify'],h['confirm_enrollment']

    def provider_ready(kind):
        keys={'card':('STRIPE_SECRET_KEY','STRIPE_WEBHOOK_SECRET'),'zoom':('ZOOM_ACCOUNT_ID','ZOOM_CLIENT_ID','ZOOM_CLIENT_SECRET','ZOOM_HOST_USER'),'email':('SENDGRID_API_KEY','SENDGRID_FROM_EMAIL')}
        provider={'card':'stripe','zoom':'zoom','email':'email'}[kind]
        return app.extensions['integrations']['ready'](provider) if request.endpoint!='stripe_webhook' else all(setting(key) for key in keys[kind])

    def rates():
        if hasattr(g,'usd_rates'):return g.usd_rates
        with db.engine.begin() as conn:
            if conn.dialect.name=='postgresql':conn.execute(text('SELECT pg_advisory_xact_lock(73592029)'))
            table=Fx.__table__;row=conn.execute(select(table).where(table.c.id==1)).mappings().first()
            if not row or aware(row['checked_at'])<now()-timedelta(hours=24 if row['rates'] and not row['failed'] else 1):
                saved=row['rates'] if row else {};asof=row['as_of'] if row else None;failed=False
                try:
                    provider=app.config.get('FX_PROVIDER')
                    if app.testing and not provider:raise ValueError('No test rate provider.')
                    saved,asof=(provider or fetch_rates)()
                except Exception:
                    failed=True
                    app.logger.warning('Currency reference refresh unavailable; keeping the last timestamped reference.')
                values={'rates':saved,'as_of':asof,'checked_at':now(),'failed':failed}
                conn.execute(table.update().where(table.c.id==1).values(**values) if row else table.insert().values(id=1,**values))
                row=values
            g.usd_rates=(row['rates'],aware(row['as_of']))
        return g.usd_rates

    def local_money(amount,country,language=None):
        if 'local_experience' in app.extensions and not session.get('admin'):
            return app.extensions['local_experience']['quote'](amount,student=g.student if g.student else None)
        language=language or (db.session.get(Preference,g.student.id).language if g.student and db.session.get(Preference,g.student.id) else session.get('ui_language','en'))
        amount=Decimal(str(amount));currency=residence(country)['currency']
        values={'usd':formatted(amount,'USD',language),'base_amount':str(amount),'currency':currency,'country':country,'as_of':None,'stale':False,'available':True,'rate':'1','amount':str(amount),'local':formatted(amount,'USD',language)}
        if currency!='USD':
            quotes,asof=rates();rate=quotes.get(currency)
            if not rate:
                values.update(available=False,local=formatted(amount,'USD',language),rate=None,amount=None)
            else:
                try:
                    rate=Decimal(str(rate))
                    if not rate.is_finite() or rate<=0:raise ValueError()
                    local=converted(amount,currency,rate)
                    values.update(local=formatted(local,currency,language),amount=str(local),rate=str(rate),as_of=asof,stale=not asof or asof<now()-timedelta(hours=48))
                except (ValueError,InvalidOperation):values.update(available=False,rate=None,amount=None)
        return values

    merchant_support={'currencies':None,'checked_at':None,'revision':None}

    def presentment(invoice,country):
        value=app.extensions['local_experience']['quote'](invoice.amount_usd)
        if not value['available'] or value['stale']:raise ValueError('Current local-currency pricing is unavailable. Confirm your country or contact support.')
        if not provider_ready('card'):raise ValueError('Card checkout is not connected. Please use a confirmed local payment method.')
        if value['currency']=='USD':return dict(currency='USD',amount=value['amount'],minor=int(Decimal(value['amount'])*100),formatted=value['local'],rate='1',as_of=value['as_of'])
        try:
            config_row=db.session.get(m['IntegrationSetting'],'stripe');config_revision=config_row.revision if config_row else 0
            if merchant_support['revision']!=config_revision or merchant_support['checked_at'] is None or merchant_support['checked_at']<now()-timedelta(hours=24):
                provider=app.config.get('STRIPE_REQUEST',stripe_request)
                account=provider('account');merchant_country=account.get('country','')
                if not isinstance(merchant_country,str) or len(merchant_country)!=2 or not merchant_country.isalpha():raise ValueError('Local card support could not be confirmed.')
                spec=provider('country_specs/'+merchant_country.upper())
                merchant_support.update(revision=config_revision,currencies={str(code).upper() for code in spec.get('supported_payment_currencies',[])},checked_at=now())
            currency=value['currency'];precision=get_currency_precision(currency)
            # Use USD when the gateway cannot present this currency or its precision.
            if currency not in merchant_support['currencies'] or precision not in (0,2):raise ValueError('This card provider does not support your local currency. Use a confirmed local payment method.')
            scale=2 if currency in ('ISK','UGX') else precision
            minor=int(Decimal(value['amount'])*(10**scale))
            if minor<=0 or minor>99999999:raise ValueError('This local amount is outside the card provider limits. Contact support.')
            return {'currency':currency,'amount':value['amount'],'minor':minor,'formatted':value['local'],'rate':value['rate'],'as_of':value['as_of']}
        except Exception:
            app.logger.warning('Local card presentment is unavailable.')
            raise ValueError('Local card checkout is unavailable. Use a confirmed local payment method.') from None

    def owner_enrollment(eid):
        enrollment=db.get_or_404(Enrollment,eid)
        if enrollment.student_id!=g.student.id:abort(403)
        return enrollment

    def invoice_for(enrollment):
        invoice=db.session.scalar(select(Invoice).where(Invoice.enrollment_id==enrollment.id))
        if not invoice:
            invoice=Invoice(enrollment_id=enrollment.id,status='existing_access' if enrollment.status=='approved' and enrollment.payment_status=='verified' else 'pending')
            db.session.add(invoice)
            try:db.session.commit()
            except IntegrityError:
                db.session.rollback();invoice=db.session.scalar(select(Invoice).where(Invoice.enrollment_id==enrollment.id))
        return invoice

    @app.get('/localization')
    def localization_info():
        country=request.args.get('country','')
        if country not in COUNTRIES or country=='Other':abort(400)
        info=residence(country);value=local_money(5,country,request.args.get('language','en'))
        value=app.extensions['local_experience']['quote'](5,country=country)
        return jsonify(country=country,currency=info['currency'],language=info['language'],timezone=info['timezone'],fee=value['local'],available=value['available'],as_of=value['as_of'].isoformat() if value['as_of'] else None,stale=value['stale'])

    @app.route('/account/location',methods=['GET','POST'])
    @student_required()
    def account_location():
        if request.method=='POST':
            try:
                country=field('country',80);language=field('language',2);zone=field('timezone',80)
                if country not in COUNTRIES or language not in ('en','es','fr') or zone not in TIMEZONES:raise ValueError('Choose your country, language and timezone.')
                g.student.country=country
                pref=db.session.get(Preference,g.student.id) or Preference(student_id=g.student.id)
                pref.language,pref.timezone=language,zone;db.session.add(pref)
                row=db.session.get(M('StudentLocale'),g.student.id) or M('StudentLocale')(student_id=g.student.id,country=country,currency=residence(country)['currency'],timezone=zone)
                row.country=country;row.currency=residence(country)['currency'];row.timezone=zone;row.country_confirmed=country!='Other';row.timezone_confirmed=True;row.timezone_manual=True;session['local_country']=country;session['browser_timezone']=zone;db.session.add(row);db.session.commit()
                flash('Residence and display currency updated. Existing invoices, saved plans and payments keep their original amounts.')
                return redirect(url_for('account_location'))
            except ValueError as error:flash(str(error))
        return render_template('account_location.html',location=residence(g.student.country),fee=local_money(5,g.student.country))

    @app.get('/payments/enrollment/<int:enrollment_id>')
    @student_required()
    def global_payment(enrollment_id):
        enrollment=owner_enrollment(enrollment_id)
        if app.extensions['program_link_plans']['ensure'](enrollment):return redirect(url_for('link_payment_plan',enrollment_id=enrollment.id))
        invoice=invoice_for(enrollment)
        return render_template('global_payment.html',e=enrollment,invoice=invoice,fee=local_money(invoice.amount_usd,g.student.country),location=residence(g.student.country),manuals=db.session.scalars(select(Manual).where(Manual.invoice_id==invoice.id).order_by(Manual.created_at.desc())).all(),card_record=db.session.scalar(select(Checkout).where(Checkout.invoice_id==invoice.id)),card_ready=provider_ready('card'),charge=None,manual_payment_methods=MANUAL_PAYMENT_METHODS)

    @app.post('/payments/verify-student')
    @student_required()
    def verify_student_number():
        number=request.form.get('student_number','').strip().upper()
        return jsonify(verified=secrets.compare_digest(number,g.student.student_number.upper())),200

    def verify_identity():
        if not secrets.compare_digest(request.form.get('student_number','').strip().upper(),g.student.student_number.upper()):raise ValueError('The student number does not match your signed-in account.')

    @app.post('/payments/<invoice_id>/manual')
    @student_required()
    def manual_fee(invoice_id):
        request.max_content_length=2*1024*1024+8192
        invoice=db.get_or_404(Invoice,invoice_id);owner_enrollment(invoice.enrollment_id)
        try:
            verify_identity()
            payment_quote=app.extensions['local_payments']['validate'](request.form.get('local_quote_id'),invoice.enrollment,expired=True)
            if payment_quote.proof_id or payment_quote.manual_id:raise ValueError('This payment quote has changed or expired. Your receipt is already submitted for review.')
            if invoice.status!='pending' and payment_quote.stage<100:raise ValueError('This invoice no longer needs a payment submission.')
            reference=field('reference',150);currency=field('currency',3);method=field('method',40)
            amount=Decimal(field('amount',25))
            if currency not in CURRENCIES or not amount.is_finite() or not 0<amount<=Decimal('999999999999.999') or amount.as_tuple().exponent < -3 or method not in MANUAL_PAYMENT_METHODS:raise ValueError('Enter a valid payment amount, currency and method.')
            if currency!=payment_quote.currency or amount!=payment_quote.amount:raise ValueError('The actual amount and currency must match your secured local payment quote. Contact support if they differ.')
            if db.session.scalar(select(Manual.id).where(Manual.reference==reference)):raise ValueError('That transaction reference was already submitted. Contact support if a correction is needed.')
            proof=request.files.get('receipt')
            if not proof or not proof.filename:raise ValueError('Attach a PDF, JPG or PNG receipt up to 2 MB.')
            data=proof.read(2*1024*1024+1)
            if not data or len(data)>2*1024*1024:raise ValueError('Receipt files must be at most 2 MB.')
            name=secure_filename(proof.filename)[:180];extension=name.rsplit('.',1)[-1].lower()
            mime={'pdf':'application/pdf','jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png'}.get(extension)
            if not mime:raise ValueError('Choose a PDF, JPG or PNG receipt.')
            if mime=='application/pdf':
                if not data.startswith(b'%PDF-'):raise ValueError('Upload a valid PDF receipt.')
            else:validate_image(data,mime)
            record=Manual(id=secrets.token_hex(16),invoice_id=invoice.id,reference=reference,currency=currency,amount=amount,method=method,note=field('note',1000,False),receipt_name=name,receipt_mime=mime)
            record.receipt_path=app.config['FILE_STORAGE'].put('receipts/'+record.id+'/'+secrets.token_hex(12),data)
            db.session.add(record);db.session.flush();payment_quote.manual_id=record.id;db.session.commit()
            flash('Receipt submitted for manual review. Class access stays locked until payment is verified and approved.')
        except (ValueError,InvalidOperation,IntegrityError) as error:
            db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'Check the payment details and transaction reference.')
        except Exception:
            db.session.rollback();flash('Receipt storage is unavailable. Please retry or contact support.')
        return redirect(url_for('global_payment',enrollment_id=invoice.enrollment_id))

    @app.get('/payments/receipts/<payment_id>')
    def receipt_file(payment_id):
        payment=db.get_or_404(Manual,payment_id)
        if not session.get('admin') and (not g.student or payment.invoice.enrollment.student_id!=g.student.id):abort(403)
        return Response(app.config['FILE_STORAGE'].get(payment.receipt_path),mimetype=payment.receipt_mime,headers={'Content-Disposition':'attachment; filename="'+payment.receipt_name+'"'})

    def settle(invoice,reference,allow_rejected=True):
        enrollment=invoice.enrollment
        # Global enrollment fees are always booked in USD, separately from full tuition.
        if not db.session.scalar(select(Entry.id).where(Entry.reference==reference)):
            entry=Entry(enrollment_id=enrollment.id,reference=reference,amount=invoice.amount_usd,currency='USD')
            db.session.add(entry);db.session.flush();db.session.add(M('FeeLedger')(entry_id=entry.id,invoice_id=invoice.id))
        if invoice.status!='pending':return
        invoice.status='paid'
        enrollment.payment_status='verified';enrollment.verified_at=now();enrollment.payment_reference=enrollment.payment_reference or reference
        if enrollment.status in ('rejected','revoked') and not allow_rejected:
            notify(enrollment.student_id,'Payment received — access review required','Your payment was received, but enrollment access requires administrator review.')
            return
        confirm(enrollment)

    @app.post('/payments/<invoice_id>/checkout')
    @student_required()
    def card_checkout(invoice_id):
        invoice=db.get_or_404(Invoice,invoice_id);owner_enrollment(invoice.enrollment_id)
        if app.extensions['program_link_plans']['existing'](invoice.enrollment):return redirect(url_for('link_payment_plan',enrollment_id=invoice.enrollment_id))
        if not provider_ready('card'):abort(503,'Card checkout is awaiting provider connection. Use manual payment or contact support.')
        try:
            verify_identity()
            if invoice.status!='pending':raise ValueError('This invoice is already settled.')
            record=db.session.scalar(select(Checkout).where(Checkout.invoice_id==invoice.id))
            if record and record.currency!=app.extensions['local_experience']['state']()['currency']:raise ValueError('An existing checkout uses a different currency. Contact support before creating another payment.')
            if record and record.session_id:
                existing=app.config.get('STRIPE_REQUEST',stripe_request)('checkout/sessions/'+quote(record.session_id,safe=''))
                if existing.get('status')=='complete':raise ValueError('Payment is awaiting provider verification. Please refresh shortly.')
                if existing.get('status')=='open' and existing.get('url','').startswith('https://checkout.stripe.com/'):return redirect(existing['url'],code=303)
                raise ValueError('Your checkout session expired. Contact support to reopen this invoice securely.')
            if not record:
                charge=presentment(invoice,g.student.country)
                record=Checkout(invoice_id=invoice.id,expected_cents=charge['minor'],currency=charge['currency'],tender_amount=Decimal(charge['amount']),fx_rate=charge['rate'],fx_as_of=charge['as_of']);db.session.add(record);db.session.commit()
            if aware(record.created_at)<now()-timedelta(hours=23):raise ValueError('Contact support to reopen this expired checkout attempt.')
            payload={'mode':'payment','adaptive_pricing[enabled]':'false','client_reference_id':g.student.student_number,'customer_email':g.student.email,'payment_method_types[0]':'card','line_items[0][price_data][currency]':record.currency.lower(),'line_items[0][price_data][unit_amount]':record.expected_cents,'line_items[0][price_data][product_data][name]':'LionTech enrollment fee — '+invoice.enrollment.course.name,'line_items[0][quantity]':1,'metadata[invoice_id]':invoice.id,'metadata[student_number]':g.student.student_number,'success_url':ORIGIN+url_for('global_payment',enrollment_id=invoice.enrollment_id)+'?checkout=returned','cancel_url':ORIGIN+url_for('global_payment',enrollment_id=invoice.enrollment_id)}
            result=app.config.get('STRIPE_REQUEST',stripe_request)('checkout/sessions',payload,'fee-'+record.id)
            if not result.get('id') or not result.get('url','').startswith('https://checkout.stripe.com/'):raise ValueError('The payment provider did not return a secure checkout.')
            record.session_id=result['id'];db.session.commit();return redirect(result['url'],code=303)
        except ValueError as error:flash(str(error))
        except Exception:db.session.rollback();flash('Card checkout is temporarily unavailable. No class access was changed.')
        return redirect(url_for('global_payment',enrollment_id=invoice.enrollment_id))

    @app.post('/webhooks/stripe')
    def stripe_webhook():
        if not provider_ready('card'):abort(503)
        try:event=signed_stripe_event(request.get_data(),request.headers.get('Stripe-Signature',''),setting('STRIPE_WEBHOOK_SECRET'))
        except ValueError:abort(400)
        if event.get('type') not in ('checkout.session.completed','checkout.session.async_payment_succeeded'):return '',200
        obj=event.get('data',{}).get('object',{});sid=obj.get('id','')
        local_result=app.extensions['local_payments']['webhook']('stripe',sid) if 'local_payments' in app.extensions else None
        if local_result is not None:return local_result
        record=db.session.scalar(select(Checkout).where(Checkout.session_id==sid).with_for_update())
        if not record:return '',200
        if record.paid:return '',200
        try:
            actual=app.config.get('STRIPE_REQUEST',stripe_request)('checkout/sessions/'+quote(sid,safe=''))
            invoice=db.session.scalar(select(Invoice).where(Invoice.id==record.invoice_id).with_for_update())
            if actual.get('id')!=sid or actual.get('payment_status')!='paid' or actual.get('currency')!=record.currency.lower() or actual.get('amount_total')!=record.expected_cents or actual.get('client_reference_id')!=invoice.enrollment.student.student_number or actual.get('metadata',{}).get('invoice_id')!=invoice.id:abort(400)
            record.needs_review=invoice.status!='pending' or invoice.enrollment.status=='rejected'
            settle(invoice,'stripe:'+sid,allow_rejected=False);record.paid=True;db.session.commit()
        except IntegrityError:db.session.rollback();return '',409
        return '',200

    @app.post('/admin/payments/<payment_id>/review')
    @admin_required
    def review_fee(payment_id):
        payment=db.session.scalar(select(Manual).where(Manual.id==payment_id).with_for_update())
        if not payment:abort(404)
        action=field('decision',20)
        if action not in ('approve','more_info'):abort(400)
        invoice=db.session.scalar(select(Invoice).where(Invoice.id==payment.invoice_id).with_for_update())
        if payment.status=='approved':return redirect(url_for('admin',view='payments'))
        if action=='approve':
            if request.form.get('funds_verified')!='yes':abort(400,'Confirm you have verified the funds against this invoice.')
            payment_quote=db.session.scalar(select(m['LocalPaymentQuote']).where(m['LocalPaymentQuote'].manual_id==payment.id).with_for_update())
            if payment_quote:
                try:app.extensions['local_payments']['validate'](payment_quote.id,invoice.enrollment,expired=True)
                except ValueError:abort(409,'This payment quote changed. Reconcile the actual funds before approval.')
                if payment.amount!=payment_quote.amount or payment.currency!=payment_quote.currency:abort(409,'Receipt amount and currency do not match the secured quote.')
                record=m['LocalCheckout'](quote_id=payment_quote.id,provider='manual',test_mode=False);db.session.add(record);db.session.flush()
                app.extensions['local_payments']['settle'](record,allow_expired=True)
            else:
                if invoice.status!='pending':abort(409,'Invoice is already settled. Review duplicate payment with support.')
                settle(invoice,'manual:'+payment.id)
            payment.status='approved'
        else:
            payment.status='more_info';notify(invoice.enrollment.student_id,'Payment needs more information',field('feedback',1000))
        db.session.commit();flash('Payment review saved.');return redirect(url_for('admin',view='payments'))

    @app.post('/admin/pricing/global')
    @admin_required
    def global_tuition():
        try:
            cid=int(field('course_id',20));db.get_or_404(M('Course'),cid)
            total=money_value(field('total',20));count=int(field('installments',2))
            if not 1<=count<=24 or total/Decimal(count)<Decimal('.01'):raise ValueError('Choose 1–24 installments with at least 0.01 USD per payment.')
            price=db.session.get(GlobalTuition,cid) or GlobalTuition(course_id=cid)
            price.total,price.installments,price.currency=total,count,'USD'
            price.full_url=m['valid_url'](field('full_url',3000,False),True);price.installment_url=m['valid_url'](field('installment_url',3000,False),True)
            db.session.add(price);db.session.commit();flash('Global USD tuition saved. All residences see its localized equivalent; existing saved plans are preserved.')
        except (ValueError,IntegrityError) as error:db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'Could not save the program price.')
        return redirect(url_for('admin',view='programs'))

    def class_detail(lesson):return db.session.get(Detail,lesson.id)

    def class_visible(lesson,student):
        enrollment=db.session.scalar(select(Enrollment).where(Enrollment.student_id==student.id,Enrollment.course_id==lesson.course_id,Enrollment.status=='approved',Enrollment.payment_status=='verified'))
        if (lesson.course.slug=='devops' or lesson.course.slug.startswith('data-analytics')) and not enrollment:return False
        detail=class_detail(lesson)
        if not detail:return True
        if not detail.published:return False
        enrollment=db.session.scalar(select(Enrollment).where(Enrollment.student_id==student.id,Enrollment.course_id==lesson.course_id))
        return bool(enrollment and (not detail.cohort_id or enrollment.cohort_id==detail.cohort_id))

    def join_open(lesson):
        detail=class_detail(lesson)
        if not detail:return True  # Preserve legacy published meetings.
        return detail.published and aware(lesson.starts_at)-timedelta(minutes=detail.join_minutes)<=now()<=aware(lesson.starts_at)+timedelta(minutes=detail.duration)

    def class_state(lesson):
        detail=class_detail(lesson)
        start=aware(lesson.starts_at)
        end=start+timedelta(minutes=detail.duration if detail else 120)
        opens=start-timedelta(minutes=detail.join_minutes if detail else 15)
        if detail and not detail.published:return 'draft'
        if now()>end:return 'ended'
        if not lesson.joining or not lesson.joining.url:return 'missing_link'
        return 'upcoming' if now()<opens else 'open'

    def calendar_text(lesson,student):
        detail=class_detail(lesson);revision=detail.revision if detail else 0
        def escape(value):return str(value).replace('\\','\\\\').replace('\n','\\n').replace(';','\\;').replace(',','\\,').replace('\r','')
        def stamp(value):return aware(value).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//LionTech Academy//Student Portal//EN','CALSCALE:GREGORIAN','METHOD:REQUEST','BEGIN:VEVENT','UID:class-'+str(lesson.id)+'@liontechacademy.com','SEQUENCE:'+str(revision),'DTSTAMP:'+stamp(now()),'DTSTART:'+stamp(lesson.starts_at),'DTEND:'+stamp(aware(lesson.starts_at)+timedelta(minutes=detail.duration if detail else 120)),'SUMMARY:'+escape(lesson.title),'DESCRIPTION:'+escape(lesson.course.name+' — join securely through your student portal. Student number: '+student.student_number),'URL:'+ORIGIN+'/classes/'+str(lesson.id),'ORGANIZER:mailto:'+(setting('SENDGRID_FROM_EMAIL') or 'liontechacademy@gmail.com'),'ATTENDEE;RSVP=TRUE:mailto:'+student.email,'STATUS:CONFIRMED','END:VEVENT','END:VCALENDAR']
        # RFC 5545 lines fold at 75 UTF-8 octets, without splitting a code point.
        folded=[]
        for line in lines:
            chunk=''
            for char in line:
                if len((chunk+char).encode('utf-8'))>74:folded.append(chunk);chunk=' '+char
                else:chunk+=char
            folded.append(chunk)
        return '\r\n'.join(folded)+'\r\n'

    def paid_for_class(student_id,lesson):
        enrollment=db.session.scalar(select(Enrollment).where(Enrollment.student_id==student_id,Enrollment.course_id==lesson.course_id,Enrollment.status=='approved',Enrollment.payment_status=='verified'))
        detail=class_detail(lesson)
        control=db.session.get(M('StudentControl'),student_id)
        return bool((not control or control.active) and enrollment and (not detail or (detail.published and (not detail.cohort_id or detail.cohort_id==enrollment.cohort_id))))

    @app.get('/live-classes')
    @student_required()
    def live_classes():
        ids=list(db.session.scalars(select(Enrollment.course_id).where(Enrollment.student_id==g.student.id)))
        lessons=[item for item in db.session.scalars(select(Class).where(Class.course_id.in_(ids)).order_by(Class.starts_at)) if class_visible(item,g.student)]
        paid={item.course_id for item in db.session.scalars(select(Enrollment).where(Enrollment.student_id==g.student.id,Enrollment.status=='approved',Enrollment.payment_status=='verified'))}
        return render_template('live_classes.html',classes=lessons,paid_course_ids=paid)

    @app.get('/calendar/classes/<int:class_id>.ics')
    @student_required()
    def class_calendar(class_id):
        lesson=db.get_or_404(Class,class_id)
        if not paid_for_class(g.student.id,lesson):abort(403)
        return Response(calendar_text(lesson,g.student),mimetype='text/calendar',headers={'Content-Disposition':'attachment; filename="liontech-class-'+str(class_id)+'.ics"'})

    def queue_class(lesson,detail):
        if not detail.published:return
        query=select(Enrollment).where(Enrollment.course_id==lesson.course_id,Enrollment.status=='approved',Enrollment.payment_status=='verified')
        if detail.cohort_id:query=query.where(Enrollment.cohort_id==detail.cohort_id)
        for enrollment in db.session.scalars(query):
            notice=notify(enrollment.student_id,'Class schedule updated',lesson.title+' — '+app.extensions['local_experience']['time'](lesson.starts_at,enrollment.student)+' — open Live Classes to view the schedule and join securely.')
            db.session.flush()
            db.session.add(m['ClassNoticeScope'](notification_id=notice.id,class_id=lesson.id))
            consent=db.session.get(M('ClassWhatsAppPreference'),enrollment.student_id)
            if consent and consent.enabled:app.extensions['notifications']['enqueue'](enrollment.student,'class_schedule',key=f'class-whatsapp:{lesson.id}:{detail.revision}:{enrollment.student_id}',channel='whatsapp',payload={'class_id':lesson.id,'revision':detail.revision})
            if not db.session.scalar(select(Delivery.id).where(Delivery.student_id==enrollment.student_id,Delivery.class_id==lesson.id,Delivery.revision==detail.revision)):
                db.session.add(Delivery(student_id=enrollment.student_id,class_id=lesson.id,revision=detail.revision))

    @app.get('/admin/live-classes')
    @admin_required
    def schedule_admin():
        selected_lesson=db.session.get(m['Lesson'],request.args.get('lesson_id',type=int)) if request.args.get('lesson_id') else None
        return render_template('schedule_admin.html',selected_lesson=selected_lesson,admin_view='classes',courses=db.session.scalars(select(M('Course'))).all(),cohorts=db.session.scalars(select(Cohort).order_by(Cohort.start_date.desc())).all(),classes=db.session.scalars(select(Class).order_by(Class.starts_at.desc()).limit(50)).all(),deliveries=db.session.scalars(select(Delivery).order_by(Delivery.id.desc()).limit(30)).all(),zoom_ready=provider_ready('zoom'),email_ready=provider_ready('email'))

    @app.post('/admin/live-classes/save')
    @admin_required
    def schedule_save():
        try:
            app.extensions['assistant']['confirm_submission']('schedule_save')
            cid=int(field('course_id',20));db.get_or_404(M('Course'),cid)
            linked_lesson=db.get_or_404(m['Lesson'],int(request.form['lesson_id'])) if request.form.get('lesson_id') else None
            if linked_lesson and linked_lesson.course_id!=cid:raise ValueError('The lesson and class must belong to the same program.')
            cohort_id=int(request.form['cohort_id']) if request.form.get('cohort_id') else None
            if cohort_id and db.get_or_404(Cohort,cohort_id).course_id!=cid:raise ValueError('Choose a cohort belonging to this program.')
            duration=int(field('duration',4));minutes=int(field('join_minutes',3));zone=field('timezone',80)
            if not 15<=duration<=600 or not 0<=minutes<=60 or zone not in TIMEZONES:raise ValueError('Choose a valid duration, join window and timezone.')
            local=datetime.fromisoformat(field('starts_at',40))
            if local.tzinfo:raise ValueError('Enter the class local time and choose its timezone.')
            zoned=local.replace(tzinfo=ZoneInfo(zone))
            if zoned.astimezone(timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None)!=local:raise ValueError('That local time does not exist due to a daylight-saving change.')
            if zoned.utcoffset()!=local.replace(tzinfo=ZoneInfo(zone),fold=1).utcoffset():raise ValueError('That local time occurs twice. Choose UTC and enter the intended UTC time.')
            from scheduling_time import weekly_occurrences
            occurrences=weekly_occurrences(local,zone,int(request.form.get('repeat_weeks','1')) if not request.form.get('class_id') else 1)
            title,instructor=field('title',150),field('instructor',100,False)
            lesson=db.get_or_404(Class,int(request.form['class_id'])) if request.form.get('class_id') else Class(course_id=cid)
            detail=class_detail(lesson) if lesson.id else None
            detail=detail or Detail()
            published=request.form.get('state')=='published'
            if request.form.get('state') not in ('draft','published'):raise ValueError('Choose draft or published.')
            mode=field('zoom_mode',15)
            if mode not in ('manual','api'):raise ValueError('Choose the Zoom connection mode.')
            link=m['valid_url'](field('join_url',3000,False),True)
            registration=m['valid_url'](field('registration_url',3000,False),True)
            if mode=='api':
                if not provider_ready('zoom'):raise ValueError('Connect Zoom account credentials before creating Zoom meetings. You can still save a manual Zoom link.')
                if not published:raise ValueError('Save drafts with manual links; create the Zoom meeting when publishing.')
                payload={'topic':title,'type':2,'start_time':zoned.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),'duration':duration,'timezone':zone,'settings':{'waiting_room':True,'join_before_host':False,'meeting_authentication':False}}
                path='meetings/'+quote(detail.meeting_id,safe='') if detail.meeting_id else 'users/'+quote(setting('ZOOM_HOST_USER'),safe='')+'/meetings'
                result=app.config.get('ZOOM_REQUEST',zoom_request)('PATCH' if detail.meeting_id else 'POST',path,payload)
                if not detail.meeting_id:
                    link=m['valid_url'](result.get('join_url',''));detail.meeting_id=str(result['id'])
                else:link=lesson.joining.url if lesson.joining else link
            if published and not link:raise ValueError('Add a Zoom joining URL or create a connected Zoom meeting before publishing.')
            lesson.course_id,lesson.title,lesson.starts_at,lesson.registration_url=cid,title,zoned.astimezone(timezone.utc),registration
            if not lesson.joining:lesson.joining=M('ClassJoinLink')(url=link)
            else:lesson.joining.url=link
            db.session.add(lesson);db.session.flush()
            if linked_lesson:
                association=db.session.get(m['LessonClass'],lesson.id) or m['LessonClass'](class_id=lesson.id)
                association.lesson_id=linked_lesson.id;db.session.add(association)
            detail.class_id,detail.cohort_id,detail.duration,detail.join_minutes=lesson.id,cohort_id,duration,minutes
            detail.timezone,detail.instructor,detail.published=zone,instructor,published
            detail.revision=(detail.revision or 0)+1;db.session.add(detail);db.session.flush()
            queue_class(lesson,detail)
            for starts_at in occurrences[1:]:
                occurrence_link=link;meeting_id=None
                if mode=='api':
                    payload['start_time']=starts_at.strftime('%Y-%m-%dT%H:%M:%SZ')
                    result=app.config.get('ZOOM_REQUEST',zoom_request)('POST','users/'+quote(setting('ZOOM_HOST_USER'),safe='')+'/meetings',payload)
                    occurrence_link=m['valid_url'](result.get('join_url',''));meeting_id=str(result['id'])
                occurrence=Class(course_id=cid,title=title,starts_at=starts_at,registration_url=registration);occurrence.joining=M('ClassJoinLink')(url=occurrence_link);db.session.add(occurrence);db.session.flush()
                occurrence_detail=Detail(class_id=occurrence.id,cohort_id=cohort_id,duration=duration,join_minutes=minutes,timezone=zone,instructor=instructor,published=published,revision=1,meeting_id=meeting_id);db.session.add(occurrence_detail);db.session.flush();queue_class(occurrence,occurrence_detail)
                if linked_lesson:db.session.add(m['LessonClass'](class_id=occurrence.id,lesson_id=linked_lesson.id))
            db.session.commit();delivered=app.extensions['integrations']['dispatch'](class_id=lesson.id)
            for occurrence in db.session.scalars(select(Class).where(Class.course_id==cid,Class.title==title,Class.starts_at.in_(occurrences[1:]))):app.extensions['integrations']['dispatch'](class_id=occurrence.id)
            flash('Class saved. Published schedule updates are in the portal. Email and calendar delivery was attempted directly for eligible students.' if delivered else 'Class saved, but some email or calendar deliveries failed. Review the delivery activity and retry outstanding updates.')
        except (ValueError,KeyError,IntegrityError) as error:
            db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'Could not save this schedule. Check the class and cohort details.')
        except Exception:
            db.session.rollback();flash('The Zoom provider did not confirm this update. The portal schedule was not changed. Check the meeting in Zoom before retrying.')
        return redirect(url_for('devops_workspace',lesson_id=linked_lesson.id) if request.form.get('lesson_id') and 'linked_lesson' in locals() and linked_lesson else url_for('schedule_admin'))

    @app.post('/admin/live-classes/deliver')
    @admin_required
    def deliver_updates():
        if not provider_ready('email'):
            flash('Email delivery awaits a SendGrid API key and verified sender. Calendar files are available in the portal.')
            return redirect(url_for('schedule_admin'))
        sent=0
        jobs=db.session.scalars(select(Delivery).where(Delivery.status.in_(('pending','failed')),Delivery.attempts<5).order_by(Delivery.id).limit(20).with_for_update(skip_locked=True)).all()
        for job in jobs:
            detail=class_detail(job.lesson)
            if not paid_for_class(job.student_id,job.lesson) or (detail and detail.revision!=job.revision):job.status='superseded';continue
            job.attempts+=1
            try:
                app.config.get('CLASS_EMAIL_SENDER',send_class_email)(job.student,job.lesson,calendar_text(job.lesson,job.student));job.status='sent';sent+=1
            except Exception:job.status='failed'
        db.session.commit();flash(f'{sent} class update email(s) sent. Failed deliveries remain available for retry.')
        return redirect(url_for('schedule_admin'))

    from flutterwave_payments import install as install_flutterwave
    app.extensions['flutterwave_payments']=install_flutterwave(app,db,m,dict(student_required=student_required,owner_enrollment=owner_enrollment,verify_identity=verify_identity,settle=settle,rates=rates))

    @app.context_processor
    def global_context():
        return dict(card_payment_records=db.session.scalars(select(Checkout).order_by(Checkout.created_at.desc())).all() if session.get('admin') else [],global_invoices=db.session.scalars(select(Invoice).order_by(Invoice.created_at.desc())).all() if session.get('admin') else [],manual_fee_records=db.session.scalars(select(Manual).order_by(Manual.created_at.desc())).all() if session.get('admin') else [],global_prices={item.course_id:item for item in db.session.scalars(select(GlobalTuition))} if session.get('admin') else {})

    return dict(rates=rates,settle_fee=settle,local_money=local_money,formatted_money=formatted,residence=residence,global_fee_usd=Decimal('5.00'),calendar_text=calendar_text,class_local_time=lambda lesson:aware(lesson.starts_at).astimezone(ZoneInfo(class_detail(lesson).timezone if class_detail(lesson) else 'UTC')).strftime('%Y-%m-%dT%H:%M'),class_detail=class_detail,class_visible=class_visible,join_open=join_open,class_state=class_state)
