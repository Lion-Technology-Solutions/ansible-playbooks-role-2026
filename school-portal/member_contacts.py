"""Operational contact details, separate from restricted HR/payroll information."""
import re
from urllib.parse import quote
from flask import abort,flash,g,redirect,render_template,request,session,url_for


def phone_number(value):
    value=value.strip()
    if not value:return ''
    value=re.sub(r'[ ()\-.]','',value)
    if not re.fullmatch(r'\+[1-9][0-9]{6,14}',value):raise ValueError('Use + and the country code, for example +14375756656.')
    return value


def define(db,now):
    class MemberContact(db.Model):
        __tablename__='portal_member_contacts'
        owner=db.Column(db.String(80),primary_key=True)
        phone=db.Column(db.String(20),nullable=False,default='')
        whatsapp=db.Column(db.String(20),nullable=False,default='')
        task_alerts=db.Column(db.Boolean,nullable=False,default=False)
        consented_at=db.Column(db.DateTime(timezone=True))
        updated_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    return dict(MemberContact=MemberContact)


def install(app,db,m):
    Contact=m['MemberContact']
    def owner():
        if session.get('admin'):return app.extensions.get('member_profiles',{}).get('contact_owner',lambda:'admin:1')()
        if g.staff:return 'staff:'+str(g.staff.id)
        if g.student:return 'student:'+g.student.id
        abort(403)
    def details(identity,fallback='',email=''):
        row=db.session.get(Contact,identity)
        try:direct=phone_number(row.phone if row else fallback)
        except ValueError:direct=''
        whatsapp=row.whatsapp if row else ''
        # Legacy registration numbers were collected as WhatsApp numbers.
        if not row and identity.startswith('student:'):whatsapp=direct
        return dict(phone=direct,whatsapp=whatsapp,email=email,call_url='tel:'+direct if direct else '',whatsapp_url='https://wa.me/'+whatsapp[1:] if whatsapp else '',email_url='mailto:'+quote(email,safe='@.+-') if email else '',task_alerts=bool(row and row.task_alerts),consented_at=row.consented_at if row else None)
    @app.route('/account/contact',methods=['GET','POST'])
    def member_contact():
        identity=owner()
        if request.method=='POST':
            try:
                direct=phone_number(request.form.get('phone',''));whatsapp=phone_number(request.form.get('whatsapp',''));enabled=request.form.get('task_alerts')=='yes'
                if enabled and (not g.staff or not whatsapp):raise ValueError('A staff WhatsApp number is required to enable task alerts.')
                if g.staff:
                    zone=request.form.get('timezone',g.staff.timezone)
                    if zone not in m['TIMEZONES']:raise ValueError('Choose a supported work time zone.')
                    db.session.get(m['StaffMember'],g.staff.id).timezone=zone
                row=db.session.get(Contact,identity) or Contact(owner=identity)
                changed=row.whatsapp!=whatsapp
                row.phone=direct;row.whatsapp=whatsapp;row.task_alerts=enabled
                row.consented_at=m['now']() if enabled and (changed or not row.consented_at) else row.consented_at if enabled else None
                row.updated_at=m['now']();db.session.add(row)
                if g.student:g.student.phone=direct or whatsapp or g.student.phone
                db.session.add(m['StaffAudit'](actor=identity,member_id=g.staff.id if g.staff else None,action='contact_preferences_updated',detail='Operational contact and task alert preferences updated.'))
                db.session.commit();g.task_alert_dirty=True;flash('Contact details saved.');return redirect(request.path)
            except ValueError as error:flash(str(error))
        email=g.staff.email if g.staff else g.student.email if g.student else g.linked_admin.email if getattr(g,'linked_admin',None) else app.extensions['admin_security']['state']().email
        return render_template('member_contact.html',contact=details(identity,g.student.phone if g.student else '',email),is_staff=bool(g.staff))
    @app.context_processor
    def context():return dict(member_contact_details=details)
    app.extensions['member_contacts']=dict(details=details,owner=owner)
