"""Explicit course teaching grants and private, downloadable lesson media."""
import re
from urllib.parse import urlsplit, parse_qs
from flask import abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select


def media_url(value):
    value=value.strip()
    try: parsed=urlsplit(value); host=(parsed.hostname or '').lower(); port=parsed.port
    except ValueError: raise ValueError('Enter a valid HTTPS YouTube or Zoom link.')
    if parsed.scheme!='https' or parsed.username or parsed.password or port not in (None,443):raise ValueError('Use a secure YouTube or Zoom link.')
    video=None
    if host in ('youtube.com','www.youtube.com','m.youtube.com'):
        if parsed.path=='/watch':video=parse_qs(parsed.query).get('v',[''])[0]
        elif parsed.path.startswith(('/embed/','/shorts/')):video=parsed.path.split('/')[2]
    elif host=='youtu.be':video=parsed.path.lstrip('/')
    if video and re.fullmatch(r'[A-Za-z0-9_-]{11}',video):return 'youtube','https://www.youtube-nocookie.com/embed/'+video
    if (host=='zoom.us' or host.endswith('.zoom.us')) and parsed.path.startswith(('/j/','/rec/')):return 'zoom',value
    raise ValueError('Use a YouTube video URL or a Zoom meeting/recording URL.')


def define(db):
    class TeachingGrant(db.Model):
        __tablename__='portal_teaching_grants'
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),primary_key=True)
        course_id=db.Column(db.Integer,db.ForeignKey('portal_courses.id'),primary_key=True)
    class TeachingUpload(db.Model):
        __tablename__='portal_teaching_uploads'
        asset_id=db.Column(db.String(32),db.ForeignKey('portal_assets.id'),primary_key=True)
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),nullable=False)
        lesson_id=db.Column(db.Integer,db.ForeignKey('portal_lessons.id'),nullable=False)
    class LessonMedia(db.Model):
        __tablename__='portal_lesson_media'
        id=db.Column(db.Integer,primary_key=True)
        lesson_id=db.Column(db.Integer,db.ForeignKey('portal_lessons.id'),nullable=False,index=True)
        title=db.Column(db.String(150),nullable=False)
        kind=db.Column(db.String(15),nullable=False)
        url=db.Column(db.Text,nullable=False)
    return dict(TeachingGrant=TeachingGrant,TeachingUpload=TeachingUpload,LessonMedia=LessonMedia)


def install(app,db,m,guards):
    Lesson, Grant, Upload, Media, Asset, File=(m[k] for k in ('Lesson','TeachingGrant','TeachingUpload','LessonMedia','Asset','LessonFile'))
    def can_teach(course_id):
        member=getattr(g,'staff',None)
        return bool(session.get('admin') or (member and member.active and member.verified_at and member.role=='instructor' and db.session.get(Grant,(member.id,course_id))))
    def lesson_for(identity):
        lesson=db.get_or_404(Lesson,identity)
        if not can_teach(lesson.course_id):abort(403)
        return lesson
    def audit(action,lesson):
        db.session.add(m['StaffAudit'](actor='admin:1' if session.get('admin') else 'staff:'+str(g.staff.id),member_id=g.staff.id if g.staff else None,action=action,detail='Lesson '+str(lesson.id)))
    @app.route('/staff/teaching',methods=['GET','POST'])
    def staff_teaching():
        if not session.get('admin') and not getattr(g,'staff',None):abort(403)
        if request.method=='POST':
            if not session.get('admin'):abort(403)
            member=db.get_or_404(m['StaffMember'],request.form.get('member_id',type=int));course=db.get_or_404(m['Course'],request.form.get('course_id',type=int))
            if member.role!='instructor' or not member.active or not member.verified_at:abort(400,'Select an active, verified instructor.')
            grant=db.session.get(Grant,(member.id,course.id))
            if request.form.get('action')=='revoke':
                if grant:db.session.delete(grant)
            elif request.form.get('action')=='grant':
                if not grant:db.session.add(Grant(member_id=member.id,course_id=course.id))
            else:abort(400)
            db.session.add(m['StaffAudit'](actor='admin:1',member_id=member.id,action='teaching_access_'+request.form['action'],detail='Course '+str(course.id)));db.session.commit();return redirect(url_for('staff_teaching'))
        lessons=[l for l in db.session.scalars(select(Lesson).order_by(Lesson.course_id,Lesson.position)) if can_teach(l.course_id)]
        return render_template('staff_teaching.html',lessons=lessons,courses=list(db.session.scalars(select(m['Course']))),instructors=list(db.session.scalars(select(m['StaffMember']).where(m['StaffMember'].role=='instructor'))),grants=list(db.session.scalars(select(Grant))) if session.get('admin') else [])
    @app.route('/staff/teaching/lessons/<int:lesson_id>',methods=['GET','POST'])
    def staff_lesson_studio(lesson_id):
        lesson=lesson_for(lesson_id)
        if request.method=='POST':
            title=request.form.get('title','').strip()
            if not 1<=len(title)<=150:abort(400,'Enter a media title.')
            try:kind,url=media_url(request.form.get('url',''))
            except ValueError as error:abort(400,str(error))
            db.session.add(Media(lesson_id=lesson.id,title=title,kind=kind,url=url));audit('lesson_media_added',lesson);db.session.commit();return redirect(request.path)
        return render_template('lesson_studio.html',lesson=lesson,media=list(db.session.scalars(select(Media).where(Media.lesson_id==lesson.id))))
    @app.post('/staff/teaching/media/<int:media_id>/remove')
    def staff_lesson_media_remove(media_id):
        row=db.get_or_404(Media,media_id);lesson=lesson_for(row.lesson_id);db.session.delete(row);audit('lesson_media_removed',lesson);db.session.commit();return redirect(url_for('staff_lesson_studio',lesson_id=lesson.id))
    @app.get('/staff/teaching/lessons/<int:lesson_id>/present')
    def staff_lesson_present(lesson_id):
        lesson=lesson_for(lesson_id)
        return render_template('lesson_present.html',lesson=lesson,media=list(db.session.scalars(select(Media).where(Media.lesson_id==lesson.id))))
    @app.post('/staff/teaching/lessons/<int:lesson_id>/assets/start')
    def staff_lesson_upload_start(lesson_id):
        lesson=lesson_for(lesson_id)
        if request.form.get('purpose')!='lesson':abort(400)
        response=app.view_functions['asset_start'].__wrapped__()
        if not hasattr(response,'get_json'):return response
        data=response.get_json()
        if data and data.get('id'):
            db.session.add(Upload(asset_id=data['id'],member_id=g.staff.id if g.staff else None,lesson_id=lesson.id)) if g.staff else None
            # Admin-created files also need a lesson association before completion.
            db.session.add(File(lesson_id=lesson.id,asset_id=data['id']));audit('lesson_upload_started',lesson);db.session.commit()
        return response
    def uploaded(lesson_id,asset_id):
        lesson=lesson_for(lesson_id);asset=db.get_or_404(Asset,asset_id)
        if not db.session.get(File,(lesson.id,asset.id)):abort(403)
        if not session.get('admin'):
            row=db.session.get(Upload,asset.id)
            if not row or row.member_id!=g.staff.id:abort(403)
        return lesson
    @app.post('/staff/teaching/lessons/<int:lesson_id>/assets/<asset_id>/chunks/<int:part>')
    def staff_lesson_upload_chunk(lesson_id,asset_id,part):
        uploaded(lesson_id,asset_id);return app.view_functions['asset_chunk'].__wrapped__(asset_id,part)
    @app.post('/staff/teaching/lessons/<int:lesson_id>/assets/<asset_id>/finish')
    def staff_lesson_upload_finish(lesson_id,asset_id):
        lesson=uploaded(lesson_id,asset_id);response=app.view_functions['asset_finish'].__wrapped__(asset_id)
        if hasattr(response,'get_json') and response.get_json().get('ready'):audit('lesson_upload_completed',lesson);db.session.commit()
        return response
    @app.context_processor
    def context():return dict(can_teach_course=can_teach,lesson_media=lambda lesson:list(db.session.scalars(select(Media).where(Media.lesson_id==lesson.id))))
    app.extensions['lesson_teaching']=dict(can_teach=can_teach)
