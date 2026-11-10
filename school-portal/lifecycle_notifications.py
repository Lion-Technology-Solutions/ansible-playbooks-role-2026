"""Transaction-bound account/payment outbox and authenticated daily reminders."""
import base64,hmac,json,os,re,secrets
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote
from urllib.error import HTTPError
from zoneinfo import ZoneInfo
from flask import abort,current_app,flash,g,has_app_context,has_request_context,jsonify,redirect,render_template,request,session,url_for
from sqlalchemy import event,inspect,select,text
from sqlalchemy.orm import Session
from providers import ORIGIN,api
from experience import installment_schedule
from whatsapp_templates import validate as validate_template,TemplateError


@event.listens_for(Session,'before_flush')
def collect_events(session_,context,instances):
    if has_app_context() and 'notifications' in current_app.extensions:current_app.extensions['notifications']['collect'](session_)


@event.listens_for(Session,'after_commit')
def publish_events(session_):
    session_.info.pop('tuition_managed',None)
    identities=session_.info.pop('lifecycle_publish',set())
    if not has_app_context():return
    publisher=current_app.config.get('LIFECYCLE_PUBLISH')
    if publisher:
        for identity in identities:publisher(identity)
    elif not current_app.testing or current_app.config.get('DIRECT_DELIVERY'):
        # The transaction must finish before delivery uses the scoped session again.
        # Request delivery runs before the response returns, after security mail.
        if has_request_context():
            g.direct_notification_ids=getattr(g,'direct_notification_ids',set()) | identities
        elif identities:
            app=current_app._get_current_object()
            with app.app_context():
                for identity in identities:
                    try:app.extensions['notifications']['process'](identity)
                    except Exception:app.logger.warning('Direct notification failed; delivery record retained.')



@event.listens_for(Session,'after_rollback')
def rollback_events(session_):
    session_.info.pop('lifecycle_publish',None);session_.info.pop('tuition_managed',None)


TITLES={
 'account_approved':('Your LionTech account is approved','Votre compte LionTech est approuvé'),
 'account_deactivated':('Your LionTech account has been deactivated','Votre compte LionTech a été désactivé'),
 'account_restored':('Your LionTech account has been reactivated','Votre compte LionTech a été réactivé'),
 'access_revoked':('Your course access has been revoked','Votre accès aux cours a été révoqué'),
 'enrollment_approved':('Your enrollment is approved','Votre inscription est approuvée'),
 'payment_verified':('Your payment has been verified','Votre paiement a été vérifié'),
 'payment_due':('LionTech payment reminder','Rappel de paiement LionTech'),
 'profile_email_changed':('Your LionTech email address was changed','Votre adresse e-mail LionTech a été modifiée'),
}
MESSAGES={
 'account_approved':('Your account is active. Class access still requires verified payment and approved enrollment.','Votre compte est actif. L’accès aux cours nécessite un paiement vérifié et une inscription approuvée.'),
 'account_deactivated':('An administrator deactivated your account. Sign-in and learning access have been stopped. Contact support for review.','Un administrateur a désactivé votre compte. La connexion et l’accès aux cours sont bloqués. Contactez l’assistance pour un examen.'),
 'account_restored':('Your account has been reactivated. Existing payment and enrollment requirements still apply.','Votre compte a été réactivé. Les conditions de paiement et d’inscription restent applicables.'),
 'access_revoked':('Access to this program has been revoked. Your account remains available for support and reviewing records.','Votre accès à ce programme a été révoqué. Votre compte reste accessible pour l’assistance et la consultation de vos dossiers.'),
 'enrollment_approved':('Your paid enrollment is approved. Sign in to view classes and authorized learning materials.','Votre inscription payée est approuvée. Connectez-vous pour consulter les cours et les documents autorisés.'),
 'payment_verified':('Your payment has been verified. Class access depends on an active approved enrollment.','Votre paiement a été vérifié. L’accès aux cours dépend d’une inscription active et approuvée.'),
 'payment_due':('A payment is due on your selected plan. Review the amount and payment options in your portal.','Un paiement est dû dans votre plan. Consultez le montant et les options de paiement dans votre portail.'),
 'profile_email_changed':('Your account email was changed. Contact support immediately if you did not authorize this.','L’e-mail de votre compte a été modifié. Contactez immédiatement l’assistance si vous n’avez pas autorisé ce changement.'),
}


def install(app,db,m,h):
    Event,Student,Control,Enrollment=(m[n] for n in ('LifecycleEvent','Student','StudentControl','Enrollment'))
    now,aware=m['now'],m['aware'];service=app.extensions['integrations']
    @app.after_request
    def send_committed_notifications(response):
        identities=getattr(g,'direct_notification_ids',set())
        g.direct_notification_ids=set()
        def deliver(identity):
            with app.app_context():
                try:process(identity)
                except Exception:
                    db.session.rollback()
                    app.logger.warning('Direct notification failed; delivery record retained.')
        if app.testing:
            for identity in identities:deliver(identity)
        elif identities:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as executor:list(executor.map(deliver,identities))
        return response

    def enqueue(student,kind,key=None,payload=None,channel='email',session_=None):
        target=session_ if session_ is not None else db.session
        row=Event(id=secrets.token_hex(16),event_key=key or kind+':'+secrets.token_hex(24),student=student,kind=kind,channel=channel,payload=payload or {})
        target.add(row);target.info.setdefault('lifecycle_publish',set()).add(row.id)
        return row
    def collect(session_):
        if 'student_management' in app.extensions:app.extensions['student_management']['guard'](session_)
        for row in list(session_.new)+list(session_.dirty):
            state=inspect(row)
            if isinstance(row,Student) and state.pending:
                enqueue(row,'account_created',channel='whatsapp',session_=session_)
                enqueue(row,'support_account_created',channel='email',session_=session_)
            elif isinstance(row,Control):
                student=session_.get(Student,row.student_id)
                if student and state.attrs.active.history.has_changes() and (row.active is False or False in state.attrs.active.history.deleted):enqueue(student,'account_deactivated' if row.active is False else 'account_restored',payload={'reason':row.reason or ''},session_=session_)
                elif student and row.reviewed_at and state.attrs.reviewed_at.history.has_changes():enqueue(student,'account_approved',session_=session_)
            elif isinstance(row,m['AuthProfile']) and row.activated_at and state.attrs.activated_at.history.has_changes():
                student=session_.get(Student,row.student_id)
                if student and (row.email_verified_at or (student.created_at and aware(student.created_at)>now()-timedelta(minutes=5))):enqueue(student,'account_approved',session_=session_)
            elif isinstance(row,Enrollment):
                if row.id in session_.info.get('tuition_managed',set()):continue
                student=session_.get(Student,row.student_id)
                if not student:continue
                payload={'enrollment_id':row.id,'course':row.course.name if row.course else ''}
                if state.attrs.payment_status.history.has_changes() and row.payment_status=='verified':enqueue(student,'payment_verified',payload=payload,session_=session_)
                if state.attrs.status.history.has_changes():
                    if row.status=='approved':enqueue(student,'enrollment_approved',payload=payload,session_=session_)
                    elif row.status in ('revoked','rejected') and ('approved' in state.attrs.status.history.deleted or row.status=='revoked'):enqueue(student,'access_revoked',payload=payload,session_=session_)
    def message(event):
        if event.kind=='support_account_created':
            student=event.student
            return 'New student account — LionTech review required',('A student account has been created.\nStudent: '+student.name+'\nStudent number: '+student.student_number+'\nReview the account and enrollment: '+ORIGIN+'/admin/students/'+student.id+'/review'+'\nAdministrator sign-in is required. Account creation does not confirm payment or grant class access.')
        if event.kind=='admin_message':return event.payload['subject'],event.payload['body']+'\n\nStudent number: '+event.student.student_number+'\n'+ORIGIN+'/enrollments'
        if event.kind=='tuition_status':
            status=event.payload.get('status','pending');course=event.payload.get('course','');reason=event.payload.get('reason','')
            enrollment=db.session.get(Enrollment,event.payload.get('enrollment_id'));control=db.session.get(Control,event.student_id)
            granted=bool(enrollment and enrollment.status=='approved' and enrollment.payment_status=='verified' and (not control or control.active))
            access='Your class access is available while your account is active.' if granted else 'Your course access is paused. Please review payment options or contact support.'
            return 'LionTech tuition update: '+status,('Hello '+event.student.name+',\nProgram: '+course+'\nTuition status: '+status+'\n'+access+'\n'+reason+'\nStudent number: '+event.student.student_number+'\nReview tuition and payment options: '+ORIGIN+'/payments/enrollment/'+str(event.payload['enrollment_id'])+'\nSupport: https://wa.me/14375756656')
        student=event.student;pref=db.session.get(m['StudentPreference'],student.id);fr=bool(pref and pref.language=='fr');data=event.payload
        title=TITLES.get(event.kind,('LionTech account update','Mise à jour du compte LionTech'))[int(fr)]
        lines=[('Bonjour ' if fr else 'Hello ')+student.name+',',MESSAGES.get(event.kind,('Your account has been updated.','Votre compte a été mis à jour.'))[int(fr)],('Numéro étudiant : ' if fr else 'Student number: ')+student.student_number]
        for key,label in (('course','Programme' if fr else 'Program'),('amount','Montant' if fr else 'Amount'),('due','Échéance' if fr else 'Due date'),('reason','Motif' if fr else 'Reason')):
            if data.get(key):lines.append(label+': '+str(data[key]))
        if event.kind=='account_approved' and not data.get('enrollment_id'):
            enrollment=db.session.scalar(select(Enrollment).where(Enrollment.student_id==student.id).order_by(Enrollment.created_at.desc()))
            if enrollment:data={**data,'enrollment_id':enrollment.id}
        lines.append(ORIGIN+'/payments/enrollment/'+str(data['enrollment_id']) if data.get('enrollment_id') else ORIGIN+('/enrollments' if event.kind=='account_approved' else '/student/login'))
        lines.append(('Mise à jour enregistrée : ' if fr else 'Update recorded: ')+aware(event.created_at).strftime('%Y-%m-%d %H:%M UTC'))
        lines.append('Support: liontechacademy@gmail.com · https://wa.me/14375756656')
        return title,'\n\n'.join(lines)
    def process(identity):
        row=db.session.scalar(select(Event).where(Event.id==identity).with_for_update().execution_options(populate_existing=True))
        if not row or row.status=='accepted' or row.attempts>=5:return
        if aware(row.created_at)<now()-timedelta(days=7):row.status='expired';db.session.commit();return
        student=row.student
        if row.kind=='tuition_status':
            enrollment=db.session.get(Enrollment,row.payload.get('enrollment_id'));tuition=db.session.get(m['TuitionControl'],row.payload.get('enrollment_id'))
            if not enrollment or enrollment.student_id!=student.id or not tuition or tuition.revision!=row.payload.get('tuition_revision') or tuition.status!=row.payload.get('status') or db.session.get(m['StudentArchive'],student.id):
                row.status='cancelled';db.session.commit();return
        if row.kind=='payment_due':
            enrollment=db.session.get(Enrollment,row.payload.get('enrollment_id'));control=db.session.get(Control,student.id)
            tuition=db.session.get(m['TuitionControl'],enrollment.id) if enrollment else None
            tuition_overdue=bool(tuition and tuition.status=='overdue' and not tuition.access_hold)
            settled=not enrollment or (control and not control.active) or enrollment.status=='rejected' or (enrollment.status=='revoked' and not tuition_overdue) or bool(tuition and tuition.status=='completed' and tuition.source=='admin')
            if row.payload.get('fee'):
                invoice=db.session.scalar(select(m['FeeInvoice']).where(m['FeeInvoice'].enrollment_id==row.payload.get('enrollment_id')))
                settled=settled or not invoice or invoice.status!='pending'
            else:
                plan=db.session.get(m['BillingSelection'],row.payload.get('enrollment_id'))
                if plan:
                    paid=sum((entry.amount for entry in db.session.scalars(select(m['PaymentEntry']).where(m['PaymentEntry'].enrollment_id==enrollment.id,m['PaymentEntry'].currency==plan.currency,~m['PaymentEntry'].id.in_(select(m['FeeLedger'].entry_id))))),Decimal(0))
                    pref=db.session.get(m['StudentPreference'],student.id);today=now().astimezone(ZoneInfo(pref.timezone if pref else 'America/Toronto')).date();balance=sum((part['amount'] for part in installment_schedule(plan.total,plan.installments,plan.starts_on) if part['due']<=today),Decimal(0))-paid
                    settled=settled or balance<=0
                    if balance>0:row.payload={**row.payload,'amount':str(balance)+' '+plan.currency}
                else:settled=True
            if settled:row.status='cancelled';db.session.commit();return
        transport=app.config.get('LIFECYCLE_HTTP',api)
        try:
            if row.channel=='email':
                if not service['ready']('email'):row.status='awaiting_email';db.session.commit();return
                values=service['config']('email');title,body=message(row);recipient=row.payload.get('recipient') or student.email
                if row.kind=='support_account_created':
                    administrator=db.session.get(m['AdminAccount'],1)
                    recipient=administrator.email if administrator else app.config.get('SCHOOL_ADMIN_EMAIL') or os.environ.get('SCHOOL_ADMIN_EMAIL') or 'liontechacademy@gmail.com'
                row.attempts+=1
                transport('https://api.sendgrid.com/v3/mail/send','POST',{'personalizations':[{'to':[{'email':recipient}]}],'from':{'email':values['SENDGRID_FROM_EMAIL']},'subject':title,'content':[{'type':'text/plain','value':body}]},{'Authorization':'Bearer '+values['SENDGRID_API_KEY']})
            else:
                values=service['config']('whatsapp')
                template=values.get('TWILIO_TEMPLATE_SID','');recipient='whatsapp:+14375756656'
                variables={'1':student.student_number,'2':'New account ready for review','3':ORIGIN+'/admin/students/'+student.id+'/review'}
                if row.kind=='class_schedule':
                    consent=db.session.get(m['ClassWhatsAppPreference'],student.id)
                    lesson=db.session.get(m['ClassSession'],row.payload.get('class_id'))
                    detail=db.session.get(m['ScheduledClass'],lesson.id) if lesson else None
                    enrollment=db.session.scalar(select(Enrollment).where(Enrollment.student_id==student.id,Enrollment.course_id==lesson.course_id,Enrollment.status=='approved',Enrollment.payment_status=='verified')) if lesson else None
                    control=db.session.get(Control,student.id)
                    if not consent or not consent.enabled or not consent.consented_at or not enrollment or (control and not control.active) or not detail or not detail.published or detail.revision!=row.payload.get('revision') or (detail.cohort_id and detail.cohort_id!=enrollment.cohort_id) or aware(lesson.starts_at)+timedelta(minutes=detail.duration)<now():
                        row.status='cancelled';db.session.commit();return
                    if not re.fullmatch(r'\+[1-9][0-9]{6,14}',consent.phone):row.status='invalid_recipient';db.session.commit();return
                    template=values.get('TWILIO_CLASS_TEMPLATE_SID','');recipient='whatsapp:'+consent.phone
                    pref=db.session.get(m['StudentPreference'],student.id);zone=pref.timezone if pref else 'America/Toronto'
                    local=aware(lesson.starts_at).astimezone(ZoneInfo(zone)).strftime('%Y-%m-%d %H:%M')+' '+zone
                    variables={'1':student.student_number,'2':lesson.title.replace('\n',' ').replace('\r',' '),'3':local,'4':ORIGIN+'/classes/'+str(lesson.id)}
                if row.kind=='tuition_status':
                    consent=db.session.get(m['TuitionWhatsAppPreference'],student.id)
                    if not consent or not consent.enabled or not consent.consented_at:
                        row.status='awaiting_student_consent';db.session.commit();return
                    if not re.fullmatch(r'\+[1-9][0-9]{6,14}',consent.phone):row.status='invalid_recipient';db.session.commit();return
                    template=values.get('TWILIO_TUITION_TEMPLATE_SID','');recipient='whatsapp:'+consent.phone
                    variables={'1':student.student_number,'2':row.payload['status'],'3':row.payload.get('course','Program').replace('\n',' ').replace('\r',' '),'4':ORIGIN+'/payments/enrollment/'+str(row.payload['enrollment_id'])}
                if not service['ready']('whatsapp') or not re.fullmatch(r'HX[0-9a-fA-F]{32}',template):
                    row.status='awaiting_whatsapp_template';db.session.commit();return
                if not re.fullmatch(r'AC[0-9a-fA-F]{32}',values.get('TWILIO_ACCOUNT_SID','')) or not re.fullmatch(r'whatsapp:\+[1-9][0-9]{6,14}',values.get('TWILIO_WHATSAPP_FROM','')):raise ValueError()
                try:
                    check=app.config.get('WHATSAPP_TEMPLATE_VALIDATOR',validate_template)
                    metadata=check(values,template,variables)
                    row.payload={**row.payload,'template_type':metadata.get('type','twilio/text')}
                except TemplateError as error:
                    row.status=error.status;db.session.commit();return
                credentials=base64.b64encode((values['TWILIO_ACCOUNT_SID']+':'+values['TWILIO_AUTH_TOKEN']).encode()).decode();row.attempts+=1
                result=transport('https://api.twilio.com/2010-04-01/Accounts/'+quote(values['TWILIO_ACCOUNT_SID'],safe='')+'/Messages.json','POST',{'From':values['TWILIO_WHATSAPP_FROM'],'To':recipient,'ContentSid':template,'ContentVariables':json.dumps(variables)},{'Authorization':'Basic '+credentials},True)
                row.provider_id=str(result.get('sid',''))[:200]
            row.status='accepted';row.accepted_at=now();db.session.commit()
        except Exception:
            row.status='failed';db.session.commit();app.logger.warning('Lifecycle notification provider handoff failed.')
            raise RuntimeError('Notification provider handoff failed.') from None
    def due_reminders():
        if 'student_management' in app.extensions:app.extensions['student_management']['sweep']()
        if db.engine.dialect.name=='postgresql':db.session.execute(text('SELECT pg_advisory_xact_lock(73592043)'))
        count=0
        for plan in db.session.scalars(select(m['BillingSelection'])).all():
            enrollment=db.session.get(Enrollment,plan.enrollment_id);student=enrollment.student;control=db.session.get(Control,student.id)
            tuition=db.session.get(m['TuitionControl'],enrollment.id)
            if (control and not control.active) or enrollment.status=='rejected' or (enrollment.status=='revoked' and (not tuition or tuition.status!='overdue' or tuition.access_hold)):continue
            pref=db.session.get(m['StudentPreference'],student.id);today=now().astimezone(ZoneInfo(pref.timezone if pref else 'America/Toronto')).date()
            paid=sum((entry.amount for entry in db.session.scalars(select(m['PaymentEntry']).where(m['PaymentEntry'].enrollment_id==enrollment.id,m['PaymentEntry'].currency==plan.currency,~m['PaymentEntry'].id.in_(select(m['FeeLedger'].entry_id))))),Decimal(0))
            parts=installment_schedule(plan.total,plan.installments,plan.starts_on);due=[part for part in parts if part['due']<=today];remaining=sum((part['amount'] for part in due),Decimal(0))-paid
            if remaining<=0 or (tuition and tuition.status=='completed' and tuition.source=='admin'):continue
            last=db.session.scalar(select(Event).where(Event.event_key.like('due:'+str(enrollment.id)+':%')).order_by(Event.created_at.desc()).limit(1))
            if last and not any(part['due']==today for part in due) and aware(last.created_at)>now()-timedelta(days=7):continue
            key='due:'+str(enrollment.id)+':'+today.isoformat()
            if db.session.scalar(select(Event.id).where(Event.event_key==key)):continue
            cumulative=Decimal(0);first_due=due[-1]['due']
            for part in due:
                cumulative+=part['amount']
                if cumulative>paid:first_due=part['due'];break
            enqueue(student,'payment_due',key,dict(enrollment_id=enrollment.id,course=enrollment.course.name,due=first_due.isoformat(),amount=str(remaining)+' '+plan.currency));count+=1
        for invoice in db.session.scalars(select(m['FeeInvoice']).where(m['FeeInvoice'].status=='pending')).all():
            enrollment=invoice.enrollment;student=enrollment.student;control=db.session.get(Control,student.id)
            tuition=db.session.get(m['TuitionControl'],enrollment.id)
            if (control and not control.active) or enrollment.status=='rejected' or (enrollment.status=='revoked' and (not tuition or tuition.status!='overdue' or tuition.access_hold)):continue
            pref=db.session.get(m['StudentPreference'],student.id);today=now().astimezone(ZoneInfo(pref.timezone if pref else 'America/Toronto')).date();due=aware(invoice.created_at).astimezone(ZoneInfo(pref.timezone if pref else 'America/Toronto')).date()
            if today<due:continue
            last=db.session.scalar(select(Event).where(Event.event_key.like('fee-due:'+invoice.id+':%')).order_by(Event.created_at.desc()).limit(1))
            if last and aware(last.created_at)>now()-timedelta(days=7):continue
            key='fee-due:'+invoice.id+':'+today.isoformat()
            if db.session.scalar(select(Event.id).where(Event.event_key==key)):continue
            enqueue(student,'payment_due',key,dict(enrollment_id=enrollment.id,course=enrollment.course.name,due=due.isoformat(),amount=str(invoice.amount_usd)+' USD',fee=True));count+=1
        db.session.commit();return count
    def drain(limit=100):
        identities=list(db.session.scalars(select(Event.id).where(Event.status.in_(['pending','failed','awaiting_email','awaiting_whatsapp_template','awaiting_student_consent','unsupported_whatsapp_template','template_fields_mismatch','template_approval_pending']),Event.attempts<5).order_by(Event.created_at).limit(limit)))
        for identity in identities:
            try:process(identity)
            except RuntimeError:pass
        return len(identities)

    @app.get('/internal/notifications/daily')
    def daily_notifications():
        secret=app.config.get('CRON_SECRET') or os.environ.get('CRON_SECRET','')
        if not secret or not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+secret):abort(401)
        return jsonify(reminders=due_reminders(),processed=drain(200))

    def refresh_delivery(limit=20,force=False):
        values=service['config']('whatsapp');result_counts={'checked':0,'failed':0,'missing':0}
        if not service['ready']('whatsapp'):
            for row in db.session.scalars(select(Event).where(Event.channel=='whatsapp',Event.status=='accepted').order_by(Event.created_at.desc()).limit(limit)):
                row.payload={**row.payload,'delivery_status':'connection_check_required','delivery_lookup_note':'Open Integrations → WhatsApp and run Test connection before checking message delivery.'}
            db.session.commit();return result_counts
        credentials=base64.b64encode((values['TWILIO_ACCOUNT_SID']+':'+values['TWILIO_AUTH_TOKEN']).encode()).decode()
        rows=list(db.session.scalars(select(Event).where(Event.channel=='whatsapp',Event.status=='accepted').order_by(Event.created_at.desc()).limit(limit)))
        for row in rows:
            checked_at=row.payload.get('delivery_checked_at')
            if checked_at and not force:
                try:
                    if (now()-aware(m['datetime'].fromisoformat(checked_at))).total_seconds()<120:continue
                except (ValueError,TypeError):pass
            payload={**row.payload,'delivery_checked_at':now().isoformat()}
            # Twilio Message resources support both SMS and multimedia SID prefixes.
            if not re.fullmatch(r'(?:SM|MM)[0-9a-fA-F]{32}',row.provider_id or ''):
                row.payload={**payload,'delivery_status':'reference_unavailable','delivery_lookup_note':'No valid Twilio message reference was saved. Review the matching message in Twilio Messaging Logs.'}
                result_counts['missing']+=1;continue
            try:
                result=app.config.get('LIFECYCLE_HTTP',api)('https://api.twilio.com/2010-04-01/Accounts/'+quote(values['TWILIO_ACCOUNT_SID'],safe='')+'/Messages/'+row.provider_id+'.json',headers={'Authorization':'Basic '+credentials})
                status=result.get('status','unknown')
                if status not in ('queued','sending','sent','delivered','read','undelivered','failed','accepted','scheduled','canceled'):status='unknown'
                count=str(result.get('num_media','unknown'))
                row.payload={**payload,'delivery_status':status,'media_count':count if count.isdigit() and len(count)<5 else 'unknown','provider_error_code':str(result.get('error_code') or '')[:12],'delivery_lookup_note':'','delivery_lookup_http':None}
                result_counts['checked']+=1
            except HTTPError as error:
                note={401:'Twilio rejected authentication. Check the saved Account SID and Auth Token.',403:'Twilio denied message-log access. Check account permissions.',404:'Twilio could not find this message under the configured account.'}.get(error.code,'Twilio could not return the message status. Retry or check Twilio Messaging Logs.')
                row.payload={**payload,'delivery_status':'lookup_failed','delivery_lookup_http':error.code,'delivery_lookup_note':note};result_counts['failed']+=1
            except Exception:
                row.payload={**payload,'delivery_status':'lookup_failed','delivery_lookup_note':'The delivery lookup could not reach Twilio. Retry the status check.'};result_counts['failed']+=1
        db.session.commit();return result_counts

    @app.route('/admin/notifications',methods=['GET','POST'])
    @h['admin_required']
    def notification_admin():
        if request.method=='POST':
            service['reauthenticate']()
            if request.form.get('action')=='refresh_delivery':
                if not service['ready']('whatsapp'):
                    refresh_delivery(limit=1);flash('Configure and test the WhatsApp connection first.')
                else:
                    counts=refresh_delivery(force=True)
                    flash(f"Delivery check complete: {counts['checked']} retrieved, {counts['failed']} failed, {counts['missing']} missing a message reference. Details appear in the table below.")
            else:due_reminders();drain(50);flash('Notifications processed. Check the delivery status for each message.')
        elif not app.testing or app.config.get('AUTO_CHECK_WHATSAPP_DELIVERY'):
            # One recent message per page view; cache for two minutes. This never sends messages.
            refresh_delivery(limit=1)
        return render_template('notification_admin.html',admin_view='notifications',events=db.session.scalars(select(Event).order_by(Event.created_at.desc()).limit(100)).all(),email_ready=service['ready']('email'),whatsapp_ready=service['ready']('whatsapp') and bool(re.fullmatch(r'HX[0-9a-fA-F]{32}',service['config']('whatsapp').get('TWILIO_TEMPLATE_SID',''))))

    @app.route('/admin/students/<student_id>/review',methods=['GET','POST'])
    @h['admin_required']
    def account_review(student_id):
        student=db.get_or_404(Student,student_id);control=db.session.get(Control,student.id)
        if request.method=='POST':
            service['reauthenticate']();action=request.form.get('action');reason=request.form.get('reason','').strip()[:500]
            if action=='approve_account':
                if not control:control=Control(student_id=student.id,active=True,revision=0);db.session.add(control)
                if not control.active:abort(400,'Reactivate this account before approving its review.')
                if not control.reviewed_at:control.reviewed_at=now()
            elif action in ('deactivate','reactivate'):
                if not control:control=Control(student_id=student.id,active=True,revision=0);db.session.add(control)
                if action=='reactivate' and db.session.get(m['StudentArchive'],student.id):abort(400,'Restore the deleted student record first.')
                active=action=='reactivate'
                if control.active!=active:
                    control.active=active;control.revision+=1;control.reason=reason
                    if not active:control.reviewed_at=None
                    if not active:db.session.query(m['AuthSession']).filter_by(student_id=student.id).delete(synchronize_session=False)
            elif action in ('approve_enrollment','revoke_access','verify_and_approve'):
                enrollment=db.get_or_404(Enrollment,request.form.get('enrollment_id',type=int))
                if enrollment.student_id!=student.id:abort(403)
                if action=='revoke_access':app.extensions['student_management']['hold'](enrollment)
                else:
                    if action=='verify_and_approve':
                        if request.form.get('funds_confirmed')!='yes' or not enrollment.payment_reference or enrollment.payment_status not in ('submitted','verified'):abort(400,'Confirm receipt of funds against the student payment reference before approval.')
                        enrollment.payment_status='verified';enrollment.verified_at=enrollment.verified_at or now()
                    h['confirm_enrollment'](enrollment)
            else:abort(400)
            db.session.commit();flash('Review saved. Related notifications are sent directly to configured providers.');return redirect(url_for('account_review',student_id=student.id))
        return render_template('account_review.html',admin_view='students',student=student,active=not control or control.active,reviewed=bool(control and control.reviewed_at),enrollments=db.session.scalars(select(Enrollment).where(Enrollment.student_id==student.id)).all())
    return dict(collect=collect,process=process,enqueue=enqueue,due_reminders=due_reminders,drain=drain)
