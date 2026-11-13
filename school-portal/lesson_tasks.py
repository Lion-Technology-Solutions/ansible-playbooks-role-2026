"""One-lesson staff assignments with private, versioned content approval."""
import hashlib,json,secrets
from datetime import date
from flask import abort,flash,g,jsonify,redirect,render_template,request,session,url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from file_storage import validate_upload
from lesson_teaching import media_url

DOCUMENTS={'outline':'Lesson outline','flyer':'Lesson flyer','presentation':'Presentation','student_manual':'Student manual','lesson_manual':'Lesson manual'}
ACTIONS={'content':'Create / update lesson content','recordings':'Add lesson recordings','schedule':'Schedule class / Zoom meeting','view':'View assigned lesson'}
def task_number(task):return f'LTA-TSK-{(task if isinstance(task,int) else task.id):06d}'


def define(db,now):
    class LessonTask(db.Model):
        __tablename__='staff_lesson_tasks'
        task_id=db.Column(db.Integer,db.ForeignKey('staff_tasks.id'),primary_key=True)
        lesson_id=db.Column(db.Integer,db.ForeignKey('portal_lessons.id'),nullable=False,index=True)
        action=db.Column(db.String(20),nullable=False)
        class_id=db.Column(db.Integer,db.ForeignKey('portal_classes.id'))
        scheduling_state=db.Column(db.String(20),nullable=False,default='not_started')
        scheduling_started_at=db.Column(db.DateTime(timezone=True))
    class LessonSubmission(db.Model):
        __tablename__='staff_lesson_submissions'
        id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
        task_id=db.Column(db.Integer,db.ForeignKey('staff_tasks.id'),nullable=False,index=True)
        preparer_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),nullable=False)
        revision=db.Column(db.Integer,nullable=False)
        status=db.Column(db.String(24),nullable=False,default='draft',index=True)
        payload=db.Column(db.JSON,nullable=False,default=dict)
        baseline=db.Column(db.String(64),nullable=False)
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
        updated_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
        submitted_at=db.Column(db.DateTime(timezone=True))
        reviewed_at=db.Column(db.DateTime(timezone=True))
        feedback=db.Column(db.Text,nullable=False,default='')
        reviewer=db.Column(db.String(100))
        __table_args__=(db.UniqueConstraint('task_id','revision'),)
    class LessonDraftAsset(db.Model):
        __tablename__='staff_lesson_draft_assets'
        asset_id=db.Column(db.String(32),db.ForeignKey('portal_assets.id'),primary_key=True)
        submission_id=db.Column(db.String(32),db.ForeignKey('staff_lesson_submissions.id'),nullable=False,index=True)
        category=db.Column(db.String(30),nullable=False)
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class ApprovedLessonResource(db.Model):
        __tablename__='portal_approved_lesson_resources'
        lesson_id=db.Column(db.Integer,db.ForeignKey('portal_lessons.id'),primary_key=True)
        category=db.Column(db.String(30),primary_key=True)
        asset_id=db.Column(db.String(32),db.ForeignKey('portal_assets.id'),nullable=False)
    return dict(LessonTask=LessonTask,LessonSubmission=LessonSubmission,LessonDraftAsset=LessonDraftAsset,ApprovedLessonResource=ApprovedLessonResource)


def install(app,db,m,h):
    Task,Assignment,Submission,Upload,Resource,Lesson,Asset=(m[k] for k in ('StaffTask','LessonTask','LessonSubmission','LessonDraftAsset','ApprovedLessonResource','Lesson','Asset'))
    now=m['now']
    def audit(action,task):db.session.add(m['StaffAudit'](actor=app.extensions['member_profiles']['audit_actor'](),member_id=task.member_id,action=action,detail=task_number(task)))
    def latest(tid):return db.session.scalar(select(Submission).where(Submission.task_id==tid).order_by(Submission.revision.desc()).limit(1))
    def active(row):
        task=db.session.get(Task,row.task_id);member=db.session.get(m['StaffMember'],row.preparer_id)
        return bool(task and member and task.member_id==member.id and member.active and member.verified_at and latest(task.id).id==row.id)
    def access(tid,write=False):
        task=db.session.scalar(select(Task).where(Task.id==tid).with_for_update())
        if not task:abort(404)
        assignment=db.get_or_404(Assignment,tid);lesson=db.get_or_404(Lesson,assignment.lesson_id)
        member=getattr(g,'staff',None)
        if not session.get('admin') and not (member and member.active and member.verified_at and task.member_id==member.id):abort(403)
        if write and (session.get('admin') or assignment.action not in ('content','recordings')):abort(403)
        return task,assignment,lesson
    def baseline(lesson):
        resources=[(r.category,r.asset_id) for r in db.session.scalars(select(Resource).where(Resource.lesson_id==lesson.id).order_by(Resource.category))]
        files=list(db.session.scalars(select(m['LessonFile'].asset_id).where(m['LessonFile'].lesson_id==lesson.id).order_by(m['LessonFile'].asset_id)))
        media=[(r.id,r.kind,r.title,r.url) for r in db.session.scalars(select(m['LessonMedia']).where(m['LessonMedia'].lesson_id==lesson.id).order_by(m['LessonMedia'].id))]
        value=dict(title=lesson.title,body=lesson.body,module=lesson.module,position=lesson.position,published=lesson.published,available=str(lesson.available_at),resources=resources,files=files,media=media)
        return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    def edit_version(row):return hashlib.sha256(json.dumps([row.id,row.status,row.payload,str(row.updated_at)],sort_keys=True).encode()).hexdigest()
    def ensure_draft(task,assignment,lesson):
        row=latest(task.id)
        if row and row.preparer_id==g.staff.id and row.status in ('draft','submitted'):return row
        previous=row
        files={r.category:r.asset_id for r in db.session.scalars(select(Resource).where(Resource.lesson_id==lesson.id))}
        payload=dict(title=lesson.title,body=lesson.body,module=lesson.module,notes='',files=files,recordings=[])
        if previous and previous.preparer_id==g.staff.id and previous.status in ('changes_requested','declined'):
            payload={**previous.payload,'files':dict(previous.payload.get('files',{})),'recordings':list(previous.payload.get('recordings',[]))}
        row=Submission(task_id=task.id,preparer_id=g.staff.id,revision=previous.revision+1 if previous else 1,baseline=baseline(lesson),payload=payload,status='draft');db.session.add(row);db.session.flush()
        return row
    def owned_asset(row,identity,category,lesson):
        asset=db.session.get(Asset,identity)
        if not asset or not asset.ready or asset.archived or asset.purpose!='lesson':raise ValueError('Complete every selected file upload before submitting.')
        upload=db.session.get(Upload,identity)
        source=db.session.get(Submission,upload.submission_id) if upload else None
        approved=db.session.get(Resource,(lesson.id,category))
        if not ((source and source.task_id==row.task_id and source.preparer_id==row.preparer_id and upload.category==category) or (approved and approved.asset_id==identity)):raise ValueError('Use files uploaded for this assigned lesson and document category.')
        if category=='recording' and not asset.mime.startswith('video/'):raise ValueError('Upload a video file for a recording.')
        return asset
    def manifest(row):
        data={**row.payload,'files':dict(row.payload.get('files',{})),'recordings':list(row.payload.get('recordings',[])),'excluded_recordings':list(row.payload.get('excluded_recordings',[]))}
        data['recordings']=[r for r in data['recordings'] if (r.get('asset_id') or r.get('url')) not in data['excluded_recordings']]
        uploads=list(db.session.scalars(select(Upload).where(Upload.submission_id==row.id).order_by(Upload.created_at,Upload.asset_id)))
        for upload in uploads:
            asset=db.session.get(Asset,upload.asset_id)
            if not asset or not asset.ready:continue
            if upload.category=='recording':
                if asset.id in data['excluded_recordings']:continue
                if not any(r.get('asset_id')==asset.id for r in data['recordings']):data['recordings'].append(dict(asset_id=asset.id,title=asset.filename))
            else:data['files'][upload.category]=asset.id
        return data
    def validate(row,assignment,lesson,data):
        if not 1<=len(data.get('title',''))<=150 or len(data.get('body',''))>100000 or len(data.get('module',''))>100:raise ValueError('Enter a lesson title and keep the description within its limits.')
        if not 1<=len(data.get('notes',''))<=5000:raise ValueError('Describe the work completed for the administrator.')
        if assignment.action=='content':
            if any(not data.get('files',{}).get(k) for k in DOCUMENTS):raise ValueError('Upload all five required documents: outline, flyer, presentation, student manual and lesson manual. Recordings are optional.')
            if len(set(data['files'][k] for k in DOCUMENTS))!=len(DOCUMENTS):raise ValueError('Use a separate file for each required document.')
            for category in DOCUMENTS:owned_asset(row,data['files'][category],category,lesson)
        elif not data.get('recordings'):raise ValueError('Add at least one recording link or uploaded video for this recording task.')
        if len(data.get('recordings',[]))>30:raise ValueError('Submit no more than 30 recordings per revision.')
        for recording in data.get('recordings',[]):
            if recording.get('asset_id'):owned_asset(row,recording['asset_id'],'recording',lesson)
            else:
                kind,url=media_url(recording.get('url',''))
                if kind=='zoom' and '/rec/' not in url:raise ValueError('Use a Zoom recording link, rather than a live meeting link.')
    def files_access(identity):
        member=getattr(g,'staff',None)
        if not member or not member.active or not member.verified_at:return False
        upload=db.session.get(Upload,identity)
        if upload:
            row=db.session.get(Submission,upload.submission_id);task=db.session.get(Task,row.task_id)
            if task.member_id==member.id:return True
        # Published resources can be read for this lesson without a course-wide grant.
        for assignment in db.session.scalars(select(Assignment).join(Task,Task.id==Assignment.task_id).where(Task.member_id==member.id)):
            if db.session.get(m['LessonFile'],(assignment.lesson_id,identity)):return True
        return False
    @app.route('/admin/lesson-tasks',methods=['GET','POST'])
    @h['admin_required']
    def admin_lesson_tasks():
        if request.method=='POST':
            member=db.get_or_404(m['StaffMember'],request.form.get('member_id',type=int));lesson=db.get_or_404(Lesson,request.form.get('lesson_id',type=int));action=request.form.get('action')
            if not member.active or not member.verified_at or action not in ACTIONS:abort(400,'Select an active, verified staff member and task action.')
            try:due=date.fromisoformat(request.form.get('due_on',''))
            except ValueError:abort(400,'Choose a task due date.')
            instructions=request.form.get('instructions','').strip()
            if len(instructions)>5000:abort(400)
            task=Task(member_id=member.id,title=(ACTIONS[action]+': '+lesson.title)[:160],description=instructions,due_on=due,priority='normal');db.session.add(task);db.session.flush()
            db.session.add(Assignment(task_id=task.id,lesson_id=lesson.id,action=action));app.extensions['task_alerts']['assigned'](task);audit('lesson_task_assigned',task)
            mail=app.extensions['staff_portal']['mail'](member,'task','task:'+str(task.id),member.email,'New lesson task · '+task_number(task),f'{task_number(task)}\n{task.title}\nOpen your assigned lesson: https://www.liontechacademy.com/staff/lesson-tasks/{task.id}')
            db.session.commit();app.extensions['staff_portal']['dispatch']([mail.id]);flash(task_number(task)+' assigned. Access is limited to this lesson.');return redirect(url_for('admin_lesson_tasks'))
        return render_template('admin_lesson_tasks.html',lessons=list(db.session.scalars(select(Lesson).order_by(Lesson.course_id,Lesson.position))),members=list(db.session.scalars(select(m['StaffMember']).where(m['StaffMember'].active.is_(True),m['StaffMember'].verified_at.is_not(None)).order_by(m['StaffMember'].name))),actions=ACTIONS,tasks=list(db.session.scalars(select(Task).join(Assignment,Assignment.task_id==Task.id).order_by(Task.id.desc()).limit(100))))
    @app.get('/staff/lesson-tasks')
    def staff_lesson_tasks():
        member=getattr(g,'staff',None)
        if not session.get('admin') and not member:abort(403)
        query=select(Task).join(Assignment,Assignment.task_id==Task.id).order_by(Task.due_on,Task.id)
        if not session.get('admin'):query=query.where(Task.member_id==member.id)
        return render_template('staff_lesson_tasks.html',tasks=list(db.session.scalars(query)),actions=ACTIONS)
    @app.route('/staff/lesson-tasks/<int:tid>',methods=['GET','POST'])
    def staff_lesson_task(tid):
        task,assignment,lesson=access(tid)
        row=latest(tid);editable=bool(g.staff and task.member_id==g.staff.id and assignment.action in ('content','recordings') and not session.get('admin'))
        if request.method=='POST':
            access(tid,True)
            try:
                action=request.form.get('action')
                if action not in ('draft','submit','begin','remove_recording'):abort(400)
                if row and request.form.get('version','')!=edit_version(row):raise ValueError('This lesson draft changed in another tab. Reload before continuing.')
                if row and row.preparer_id==g.staff.id and row.status=='submitted':raise ValueError('This version is locked while it awaits administrator review.')
                row=ensure_draft(task,assignment,lesson)
                if action=='remove_recording':
                    data=manifest(row);identity=request.form.get('recording_identity','')
                    if not identity or not any((r.get('asset_id') or r.get('url'))==identity for r in data['recordings']):raise ValueError('Choose a recording in this current draft.')
                    data['recordings']=[r for r in data['recordings'] if (r.get('asset_id') or r.get('url'))!=identity]
                    data['excluded_recordings']=list(set(data['excluded_recordings']+[identity]));row.payload=data
                elif action!='begin':
                    data=manifest(row)
                    data.update({k:request.form.get(k,'').strip() for k in ('title','body','module','notes')})
                    link=request.form.get('recording_url','').strip()
                    if link:
                        kind,url=media_url(link)
                        if kind=='zoom' and '/rec/' not in url:raise ValueError('Use a Zoom recording URL.')
                        data['excluded_recordings']=[identity for identity in data['excluded_recordings'] if identity!=url]
                        if not any(r.get('url')==url for r in data['recordings']):data['recordings'].append(dict(kind=kind,url=url,title=request.form.get('recording_title','').strip()[:150] or 'Lesson recording'))
                    if action=='submit':
                        validate(row,assignment,lesson,data)
                        if row.baseline!=baseline(lesson):raise ValueError('The published lesson changed. Ask the administrator to return this draft for a fresh revision before submitting.')
                        row.status='submitted';row.submitted_at=now();task.status='in_progress';app.extensions['lesson_review_notices']['enqueue'](row,task,lesson)
                    row.payload=data
                row.updated_at=now();audit('lesson_submission_'+action,task);db.session.commit();flash('Lesson submitted for administrator review.' if action=='submit' else 'Private lesson draft saved.');return redirect(request.path)
            except (ValueError,IntegrityError) as error:
                db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'Another draft update was saved. Reload before continuing.');row=latest(tid)
        visible_row=row if not row or session.get('admin') or (g.staff and row.preparer_id==g.staff.id) else None
        values=manifest(visible_row) if visible_row else dict(title=lesson.title,module=lesson.module,body=lesson.body,notes='',files={},recordings=[])
        return render_template('staff_lesson_task.html',task=task,assignment=assignment,lesson=lesson,row=visible_row,values=values,editable=editable,version=edit_version(row) if row else '',documents=DOCUMENTS,history=list(db.session.scalars(select(Submission).where(Submission.task_id==tid).order_by(Submission.revision.desc()))),cohorts=list(db.session.scalars(select(m['Cohort']).where(m['Cohort'].course_id==lesson.course_id))),actions=ACTIONS)
    def upload_for(tid,sid,identity=None):
        task,assignment,lesson=access(tid,True)
        row=db.get_or_404(Submission,sid)
        if row.task_id!=tid or row.preparer_id!=g.staff.id or row.status!='draft' or latest(tid).id!=row.id:abort(403,'Only the current assigned draft can receive uploads.')
        if identity:
            upload=db.get_or_404(Upload,identity)
            if upload.submission_id!=sid:abort(403)
        return task,assignment,lesson,row
    @app.post('/staff/lesson-tasks/<int:tid>/drafts/<sid>/assets/start')
    def lesson_task_upload_start(tid,sid):
        task,assignment,lesson,row=upload_for(tid,sid);category=request.form.get('category')
        if category not in (*DOCUMENTS,'recording') or (assignment.action=='recordings' and category!='recording'):abort(400)
        try:
            size=int(request.form.get('size','0'));name,mime=validate_upload(request.form.get('filename',''),size,'lesson')
            if category=='recording' and not mime.startswith('video/'):raise ValueError('Choose a video file, or add a YouTube/Zoom recording link.')
            if category!='recording' and mime.startswith(('video/','audio/')):raise ValueError('Use the recording section for video; upload a document for this deliverable.')
            asset=Asset(title=name,filename=name,mime=mime,size=size,purpose='lesson');db.session.add(asset);db.session.flush();db.session.add(Upload(asset_id=asset.id,submission_id=sid,category=category));row.updated_at=now();db.session.commit()
            from file_storage import CHUNK_SIZE
            return jsonify(id=asset.id,chunk_size=CHUNK_SIZE,parts=(size+CHUNK_SIZE-1)//CHUNK_SIZE)
        except (ValueError,IntegrityError) as error:db.session.rollback();return jsonify(error=str(error)),400
    @app.post('/staff/lesson-tasks/<int:tid>/drafts/<sid>/assets/<identity>/chunks/<int:part>')
    def lesson_task_upload_chunk(tid,sid,identity,part):
        upload_for(tid,sid,identity);return app.view_functions['asset_chunk'].__wrapped__(identity,part)
    @app.post('/staff/lesson-tasks/<int:tid>/drafts/<sid>/assets/<identity>/finish')
    def lesson_task_upload_finish(tid,sid,identity):
        upload_for(tid,sid,identity);return app.view_functions['asset_finish'].__wrapped__(identity)
    @app.get('/admin/lesson-submissions')
    @h['admin_required']
    def admin_lesson_submissions():
        return render_template('admin_lesson_submissions.html',rows=list(db.session.scalars(select(Submission).where(Submission.status!='draft').order_by(Submission.submitted_at.desc()))),active=active)
    @app.route('/admin/lesson-submissions/<identity>',methods=['GET','POST'])
    @h['admin_required']
    def admin_lesson_submission(identity):
        initial=db.get_or_404(Submission,identity);task,assignment,lesson=access(initial.task_id)
        lesson=db.session.scalar(select(Lesson).where(Lesson.id==lesson.id).with_for_update().execution_options(populate_existing=True))
        row=db.session.scalar(select(Submission).where(Submission.id==identity).with_for_update().execution_options(populate_existing=True))
        if row.status=='draft':abort(404)
        issue='' if active(row) and row.baseline==baseline(lesson) else 'The assignment or published lesson changed. Request a corrected revision before approval.'
        if request.method=='POST':
            app.extensions['integrations']['reauthenticate']()
            try:
                if row.status!='submitted':raise ValueError('This version has already been reviewed.')
                decision=request.form.get('decision');feedback=request.form.get('feedback','').strip()[:2000]
                if decision not in ('approved','changes_requested','declined'):abort(400)
                if decision!='approved' and not feedback:raise ValueError('Record feedback for the assigned staff member.')
                if decision=='approved':
                    if issue:raise ValueError(issue)
                    if request.form.get('confirm_content')!='yes':raise ValueError('Confirm you reviewed the documents, recordings and lesson details.')
                    validate(row,assignment,lesson,row.payload)
                    if assignment.action=='content':
                        lesson.title=row.payload['title'];lesson.body=row.payload['body'];lesson.module=row.payload['module'];lesson.published=True;lesson.available_at=now()
                        for category in DOCUMENTS:
                            identity=row.payload['files'][category];resource=db.session.get(Resource,(lesson.id,category))
                            old=resource.asset_id if resource else None
                            if not resource:resource=Resource(lesson_id=lesson.id,category=category,asset_id=identity);db.session.add(resource)
                            else:resource.asset_id=identity
                            if not db.session.get(m['LessonFile'],(lesson.id,identity)):db.session.add(m['LessonFile'](lesson_id=lesson.id,asset_id=identity))
                            if old and old!=identity:
                                previous=db.session.get(m['LessonFile'],(lesson.id,old))
                                if previous:db.session.delete(previous)
                    elif not lesson.published:raise ValueError('Approve the lesson content before publishing recordings.')
                    for recording in row.payload.get('recordings',[]):
                        if recording.get('asset_id'):
                            if not db.session.get(m['LessonFile'],(lesson.id,recording['asset_id'])):db.session.add(m['LessonFile'](lesson_id=lesson.id,asset_id=recording['asset_id']))
                        elif not db.session.scalar(select(m['LessonMedia'].id).where(m['LessonMedia'].lesson_id==lesson.id,m['LessonMedia'].url==recording['url'])):db.session.add(m['LessonMedia'](lesson_id=lesson.id,title=recording['title'],kind=recording['kind'],url=recording['url']))
                    task.status='completed';task.result='Lesson submission approved and published.';task.updated_at=now()
                    for enrollment in db.session.scalars(select(m['Enrollment']).where(m['Enrollment'].course_id==lesson.course_id,m['Enrollment'].status=='approved',m['Enrollment'].payment_status=='verified')):
                        control=db.session.get(m['StudentControl'],enrollment.student_id)
                        if not db.session.get(m['StudentArchive'],enrollment.student_id) and (not control or control.active):db.session.add(m['Notification'](student_id=enrollment.student_id,title='Lesson resources updated',body=lesson.title+' · Approved resources are available in your program.'))
                row.status=decision;row.feedback=feedback;row.reviewed_at=now();row.updated_at=now();row.reviewer=app.extensions['member_profiles']['audit_actor']();audit('lesson_submission_'+decision,task);db.session.commit();flash('Lesson '+('approved and published for eligible enrolled students.' if decision=='approved' else decision.replace('_',' ')+'.'));return redirect(request.path)
            except (ValueError,IntegrityError) as error:db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'The lesson changed in another session. Reload before reviewing.')
        return render_template('admin_lesson_submission.html',task=task,assignment=assignment,lesson=lesson,row=row,values=row.payload,documents=DOCUMENTS,issue=issue,notices=list(db.session.scalars(select(m['LessonReviewNotice']).where(m['LessonReviewNotice'].submission_id==row.id))))
    @app.context_processor
    def context():return dict(staff_task_by_id=lambda identity:db.session.get(Task,identity),task_number=task_number,lesson_task=lambda tid:db.session.get(Assignment,tid),lesson_task_lesson=lambda tid:db.session.get(Lesson,db.session.get(Assignment,tid).lesson_id),lesson_submission_latest=latest,lesson_submission_asset=lambda identity:db.session.get(Asset,identity),lesson_task_actions=ACTIONS,lesson_submission_active=active)
    app.extensions['lesson_tasks']=dict(access=access,files_access=files_access,latest=latest,active=active,baseline=baseline)
