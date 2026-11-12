"""Server-enforced idle sessions and isolated administrator role previews."""
import hashlib
import secrets
from datetime import timedelta
from flask import abort, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select, update

ROLES = {
    'student': dict(name='Student', title='Your learning workspace', section='My learning',
        cards=[('Learning progress','4 of 12 sample lessons completed'),('Next class','Sample class · schedule placeholder'),('Payments','Sample enrollment confirmed')],
        action='Sample lesson resources', description='Browse a sample learning path, class card and enrollment status.'),
    'instructor': dict(name='Instructor', title='Your teaching workspace', section='Assigned courses',
        cards=[('Assigned courses','DevOps and Data Analytics · sample data'),('Next lesson','Week 1 · draft sample lesson'),('Reviews','3 sample assignments awaiting review')],
        action='Schedule sample lesson', description='Review the teaching layout, lesson resources and assessment controls.'),
    'employee': dict(name='Team Member', title='Your team workspace', section='Daily reports',
        cards=[('Daily report','Sample report · not submitted'),('Team','Student Operations · sample team'),('Supervisor','Sample supervisor')],
        action='Submit sample report', description='Review the proposed team dashboard and daily report presentation.'),
}


def install(app, db, m):
    Activity, Audit = m['PortalSessionActivity'], m['RolePreviewAudit']
    def clock():
        return app.config.get('SESSION_CLOCK', m['now'])()
    def actor():
        if session.get('admin'):
            return 'admin:1'
        if session.get('staff_id'):
            return 'staff:'+str(session['staff_id'])
        if session.get('student_id'):
            return 'student:'+session['student_id']
        return None
    def login_endpoint(identity):return 'admin_login' if identity=='admin:1' else 'staff_login' if identity and identity.startswith('staff:') else 'login'
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()
    def record():
        token=session.get('portal_session')
        return db.session.get(Activity,digest(token)) if isinstance(token,str) else None
    def end():
        row=record()
        if row:
            row.revoked=True;db.session.commit()
        session.clear();g.student=None;g.staff=None
    def failure(reason, previous):
        end()
        destination=url_for(login_endpoint(previous),reason=reason)
        if request.endpoint in ('session_status','session_activity','session_end') or request.is_json:
            return jsonify(authenticated=False,reason=reason,login_url=destination),401
        return redirect(destination,303 if request.method!='GET' else 302)
    def guard():
        current=actor();g.portal_actor_before=current
        if not current:
            return
        # This flag is set only by the authenticated, scoped API's internal dispatcher.
        # API keys and server jobs keep their own authorization and are not browser sessions.
        if getattr(g,'portal_internal_delegation',False):return
        row=record();instant=clock()
        if not row or row.actor!=current or row.revoked:
            return failure('signed-out',current)
        if m['aware'](row.last_active_at)+timedelta(minutes=15)<=instant or m['aware'](row.absolute_expires_at)<=instant:
            return failure('inactive',current)
        g.portal_activity=row
        # Explicit navigation counts as activity; asset loads and background fetches do not.
        if request.method=='GET' and request.headers.get('Sec-Fetch-Mode')=='navigate' and request.headers.get('Sec-Fetch-Dest')=='document':
            row.last_active_at=instant;db.session.commit()
        mode=session.get('role_preview')
        if mode and request.endpoint not in ('role_preview','role_switcher','role_switch','role_preview_exit',
                'session_status','session_activity','session_end','logout','profile_picture','branding_css','brand_image','static','health'):
            if request.method not in ('GET','HEAD'):
                return jsonify(error='Live changes are disabled in role preview. Return to Admin first.'),403
            return redirect(url_for('role_preview',role=mode))
    @app.after_request
    def establish(response):
        current=actor()
        # Authentication handlers clear the old session; issue a fresh server-side activity nonce.
        if current and not session.get('portal_session') and not getattr(g,'portal_actor_before',None) and response.status_code<400:
            token=secrets.token_urlsafe(32);instant=clock()
            row=Activity(digest=digest(token),actor=current,channel=secrets.token_hex(12),
                last_active_at=instant,absolute_expires_at=instant+timedelta(hours=4),revoked=False)
            db.session.add(row);db.session.commit();session['portal_session']=token
        return response
    def status():
        row=getattr(g,'portal_activity',None) or record()
        if not row or not actor():
            return jsonify(authenticated=False,login_url=url_for('login')),401
        expires=min(m['aware'](row.last_active_at)+timedelta(minutes=15),m['aware'](row.absolute_expires_at))
        return jsonify(authenticated=True,expires_at=expires.isoformat(),server_now=clock().isoformat(),channel=row.channel,
            login_url=url_for(login_endpoint(actor())),warning_seconds=60)
    @app.get('/session/status')
    def session_status():
        return status()
    @app.post('/session/activity')
    def session_activity():
        if not actor():
            return jsonify(authenticated=False,login_url=url_for('login')),401
        row=getattr(g,'portal_activity',None) or record();instant=clock()
        if not row:
            return failure('signed-out',actor())
        result=db.session.execute(update(Activity).where(Activity.digest==row.digest,Activity.revoked.is_(False),
            Activity.last_active_at>instant-timedelta(minutes=15),Activity.absolute_expires_at>instant).values(last_active_at=instant).execution_options(synchronize_session=False))
        db.session.commit()
        if not result.rowcount:
            return failure('inactive',actor())
        db.session.refresh(row)
        return status()
    @app.post('/session/end')
    def session_end():
        previous=actor();end()
        return jsonify(authenticated=False,login_url=url_for(login_endpoint(previous),reason='signed-out'))
    def require_admin():
        if not session.get('admin'):
            abort(403)
    def audit(previous,target):
        row=record()
        db.session.add(Audit(session_digest=row.digest,previous_role=previous or 'admin',target_role=target,created_at=clock()))
        db.session.commit()
    @app.get('/admin/profile-switch')
    def role_switcher():
        require_admin()
        return render_template('role_switcher.html',roles=ROLES,admin_view='profile')
    @app.post('/admin/profile-switch')
    def role_switch():
        require_admin()
        role=request.form.get('role')
        if role not in ROLES and role!='admin':
            abort(400,'Choose Student, Instructor, Team Member or Administrator.')
        previous=session.get('role_preview');audit(previous,role)
        if role=='admin':
            session.pop('role_preview',None)
            return redirect(url_for('admin'))
        session['role_preview']=role
        return redirect(url_for('role_preview',role=role))
    @app.get('/admin/preview/<role>')
    def role_preview(role):
        require_admin()
        if role not in ROLES:
            abort(404)
        if session.get('role_preview')!=role:
            return redirect(url_for('role_switcher'))
        return render_template('role_preview.html',preview=ROLES[role],role=role,admin_view='profile')
    @app.post('/admin/preview/exit')
    def role_preview_exit():
        require_admin();audit(session.get('role_preview'),'admin');session.pop('role_preview',None)
        return redirect(url_for('admin'))
    @app.context_processor
    def context():
        current=actor();row=(getattr(g,'portal_activity',None) or record()) if current else None
        return dict(preview_role=session.get('role_preview'),preview_role_name=ROLES.get(session.get('role_preview'),{}).get('name'),
            session_activity_enabled=bool(current),session_channel=row.channel if row else '',session_login_url=url_for(login_endpoint(current)))
    app.extensions['profile_sessions']=dict(guard=guard,end=end,roles=ROLES)
