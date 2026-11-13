"""Student-controlled financial WhatsApp/SMS alerts using configured Twilio senders."""
import base64,re
from urllib.parse import quote
from flask import abort,flash,g,redirect,render_template,request,url_for
from sqlalchemy import select


def define(db,now):
    class PaymentSMSPreference(db.Model):
        __tablename__='portal_payment_sms_preferences'
        student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),primary_key=True)
        phone=db.Column(db.String(20),nullable=False)
        enabled=db.Column(db.Boolean,nullable=False,default=False)
        consented_at=db.Column(db.DateTime(timezone=True))
    return dict(PaymentSMSPreference=PaymentSMSPreference)


def install(app,db,m,h):
    SMS,WA=m['PaymentSMSPreference'],m['TuitionWhatsAppPreference']
    def channels(student):
        result=[]
        for channel,model in (('whatsapp',WA),('sms',SMS)):
            row=db.session.get(model,student.id)
            if row and row.enabled and row.consented_at:result.append(channel)
        return result
    def sms(event,transport,message):
        preference=db.session.get(SMS,event.student_id)
        if not preference or not preference.enabled or not preference.consented_at:event.status='awaiting_student_consent';return
        values=app.extensions['integrations']['config']('whatsapp');sender=values.get('TWILIO_PAYMENT_SMS_FROM','')
        if not re.fullmatch(r'\+[1-9][0-9]{6,14}',sender) or not re.fullmatch(r'AC[0-9a-fA-F]{32}',values.get('TWILIO_ACCOUNT_SID','')) or not values.get('TWILIO_AUTH_TOKEN'):event.status='awaiting_sms_sender';return
        if not re.fullmatch(r'\+[1-9][0-9]{6,14}',preference.phone):event.status='invalid_recipient';return
        title,body=message(event);credentials=base64.b64encode((values['TWILIO_ACCOUNT_SID']+':'+values['TWILIO_AUTH_TOKEN']).encode()).decode()
        event.attempts+=1
        result=transport('https://api.twilio.com/2010-04-01/Accounts/'+quote(values['TWILIO_ACCOUNT_SID'],safe='')+'/Messages.json','POST',{'From':sender,'To':preference.phone,'Body':(title+'\n'+body)[:1500]},{'Authorization':'Basic '+credentials},True)
        event.provider_id=str(result.get('sid',''))[:200];event.status='accepted';event.accepted_at=m['now']()
    @app.route('/account/payment-alerts',methods=['GET','POST'])
    @h['student_required']()
    def payment_alert_preferences():
        if request.method=='POST':
            phone=request.form.get('phone','').strip()
            if not re.fullmatch(r'\+[1-9][0-9]{6,14}',phone):abort(400,'Use an international phone number beginning with + and the country code.')
            for key,model in (('whatsapp',WA),('sms',SMS)):
                row=db.session.get(model,g.student.id) or model(student_id=g.student.id,phone=phone)
                row.phone=phone;row.enabled=request.form.get(key)=='yes';row.consented_at=m['now']() if row.enabled else None;db.session.add(row)
            db.session.commit();flash('Payment alert preferences saved. Email reminders remain available; opted-in phone alerts require a configured sender.');return redirect(url_for('payment_alert_preferences'))
        return render_template('payment_alert_preferences.html',sms=db.session.get(SMS,g.student.id),whatsapp=db.session.get(WA,g.student.id))
    app.extensions['payment_alerts']=dict(channels=channels,sms=sms)
