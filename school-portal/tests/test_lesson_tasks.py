from datetime import date,timedelta
import re
import pytest
from sqlalchemy import select
from app import db,Lesson,StaffTask,LessonTask,LessonSubmission,LessonDraftAsset,ApprovedLessonResource,LessonFile,LessonMedia,Asset,ClassSession,Enrollment,LessonReviewNotice,StaffMember
from lesson_tasks import DOCUMENTS,task_number
from test_staff_portal import setup,create,login,post
from test_lessons_files import setup as learner_setup
from test_portal import csrf


def scenario(app,action='content'):
    manager,student,cid,cohort=learner_setup(app);manager,sent=setup(app);member=create(app,'Content Officer');worker=login(app,sent,member)
    with app.app_context():
        lesson=Lesson(course_id=cid,title='Linux foundations',body='Published original',published=True);other=Lesson(course_id=cid,title='Unassigned networking',body='Other private lesson',published=False)
        db.session.add_all([lesson,other]);db.session.commit();lid,other_id=lesson.id,other.id
    response=post(manager,'/admin/lesson-tasks',page='/admin/team',member_id=member,lesson_id=lid,action=action,due_on=(date.today()+timedelta(days=7)).isoformat(),instructions='Prepare only Linux resources.');assert response.status_code==200,response.data[-1000:]
    with app.app_context():tid=db.session.scalar(select(LessonTask.task_id))
    return manager,student,worker,lid,tid,member,sent,cid,cohort,other_id


def draft(worker,tid):
    path=f'/staff/lesson-tasks/{tid}';page=worker.get(path);version=re.search(rb'name="version" value="([^"]*)"',page.data)[1].decode()
    return worker.post(path,data={'csrf':csrf(worker,path),'version':version,'action':'begin'},follow_redirects=True)


def submission_id(app):
    with app.app_context():return db.session.scalar(select(LessonSubmission.id).order_by(LessonSubmission.revision.desc()))


def upload(app,worker,tid,category,name=None,data=b'%PDF-1.7 Example test resource'):
    sid=submission_id(app);path=f'/staff/lesson-tasks/{tid}/drafts/{sid}/assets';token=csrf(worker,f'/staff/lesson-tasks/{tid}')
    result=worker.post(path+'/start',data={'csrf':token,'category':category,'filename':name or category+'.pdf','size':len(data)})
    assert result.status_code==200,result.data[-1000:]
    aid=result.json['id'];assert worker.post(path+'/'+aid+'/chunks/0',data=data,headers={'X-CSRF-Token':token,'Content-Type':'application/octet-stream'}).status_code==200
    assert worker.post(path+'/'+aid+'/finish',headers={'X-CSRF-Token':token}).json['ready']
    return aid


def submit(worker,tid,**extra):
    path=f'/staff/lesson-tasks/{tid}';page=worker.get(path);version=re.search(rb'name="version" value="([^"]*)"',page.data)[1].decode()
    values={'csrf':csrf(worker,path),'version':version,'action':'submit','title':'Linux foundations updated','module':'Linux','body':'Reviewed shell exercises','notes':'Completed the lesson package.'};values.update(extra)
    return worker.post(path,data=values,follow_redirects=True)


def decision(manager,identity,**extra):
    values=dict(decision='approved',confirm_content='yes',admin_password='test-admin-password',feedback='');values.update(extra)
    return post(manager,'/admin/lesson-submissions/'+identity,page='/admin/team',**values)


def package(app,worker,tid):
    draft(worker,tid)
    return {key:upload(app,worker,tid,key) for key in DOCUMENTS}


def test_exact_lesson_assignment_required_documents_private_until_admin_approval(app):
    manager,student,worker,lid,tid,member,sent,cid,cohort,other_id=scenario(app)
    assert task_number(tid).encode() in worker.get('/staff/lesson-tasks').data
    assert b'Unassigned networking' not in worker.get('/staff/lesson-tasks').data
    assert b'Open assigned lesson' in worker.get('/staff/workboard').data
    assert worker.get('/admin/lesson-tasks').status_code==302
    files=package(app,worker,tid)
    with app.app_context():
        assert not db.session.scalar(select(LessonFile))
        assert db.session.get(Lesson,lid).body=='Published original'
    for aid in files.values():
        assert worker.get('/files/'+aid).status_code==200
        assert student.get('/files/'+aid).status_code==403
    result=submit(worker,tid);assert result.status_code==200 and b'Awaiting administrator review' in result.data
    identity=submission_id(app)
    with app.app_context():
        assert db.session.get(LessonSubmission,identity).status=='submitted'
        assert len(list(db.session.scalars(select(LessonReviewNotice))))==2
        assert db.session.get(Lesson,lid).body=='Published original'
        assert not db.session.scalar(select(LessonFile))
        assert not db.session.get(LessonSubmission,identity).payload['recordings']
    assert worker.get('/admin/lesson-submissions/'+identity).status_code==302
    assert b'Linux foundations' in manager.get('/admin/lesson-submissions/'+identity).data
    assert decision(manager,identity).status_code==200
    with app.app_context():
        assert db.session.get(Lesson,lid).body=='Reviewed shell exercises'
        assert db.session.get(LessonSubmission,identity).status=='approved'
        assert db.session.get(StaffTask,tid).status=='completed'
        assert len(list(db.session.scalars(select(ApprovedLessonResource))))==5
        assert len(list(db.session.scalars(select(LessonFile))))==5
        e=Enrollment(student_id=db.session.scalar(select(__import__('app').Student.id)),course_id=cid,cohort_id=cohort,status='approved',payment_status='verified');db.session.add(e);db.session.commit()
    assert student.get('/lessons/'+str(lid)).status_code==200
    assert student.get('/files/'+files['outline']).status_code==200
    assert student.get('/lessons/'+str(other_id)).status_code==404
    assert worker.post(f'/staff/lesson-tasks/{tid}/drafts/{identity}/assets/start',data={'csrf':csrf(worker,'/staff'),'category':'outline','filename':'forged.pdf','size':4}).status_code==403


def test_missing_documents_block_submission_but_recordings_are_optional(app):
    _,_,worker,lid,tid,*_=scenario(app);draft(worker,tid);upload(app,worker,tid,'outline')
    result=submit(worker,tid);assert b'all five required documents' in result.data
    with app.app_context():
        assert db.session.scalar(select(LessonSubmission)).status=='draft'
        assert not db.session.scalar(select(LessonReviewNotice))


def test_recording_only_task_can_be_submitted_after_class_and_needs_approval(app):
    manager,student,worker,lid,tid,*_=scenario(app,'recordings');draft(worker,tid)
    result=submit(worker,tid);assert b'at least one recording' in result.data
    aid=upload(app,worker,tid,'recording','linux.mp4',b'raw video example')
    result=submit(worker,tid,recording_url='https://youtu.be/dQw4w9WgXcQ',recording_title='Linux class recording');assert b'Awaiting administrator review' in result.data
    with app.app_context():
        assert not db.session.scalar(select(LessonMedia)) and not db.session.scalar(select(LessonFile))
    identity=submission_id(app);decision(manager,identity)
    with app.app_context():
        assert db.session.scalar(select(LessonMedia)).kind=='youtube'
        assert db.session.get(LessonFile,(lid,aid))
        assert db.session.get(Lesson,lid).body=='Published original'


@pytest.mark.parametrize('link',['https://example.org/video','javascript:alert(1)','https://zoom.us/j/123','https://youtu.be/not-video'])
def test_recording_links_reject_unsupported_or_live_meeting_urls(app,link):
    _,_,worker,lid,tid,*_=scenario(app,'recordings');draft(worker,tid)
    result=submit(worker,tid,recording_url=link)
    with app.app_context():assert db.session.scalar(select(LessonSubmission)).status=='draft'


@pytest.mark.parametrize('field,value',[('admin_password','wrong'),('confirm_content','')])
def test_admin_review_requires_independent_confirmation_and_reauthentication(app,field,value):
    manager,_,worker,lid,tid,*_=scenario(app);package(app,worker,tid);submit(worker,tid);identity=submission_id(app)
    result=decision(manager,identity,**{field:value});assert result.status_code in (200,403)
    with app.app_context():assert db.session.get(Lesson,lid).body=='Published original' and db.session.get(LessonSubmission,identity).status=='submitted'


@pytest.mark.parametrize('outcome',['changes_requested','declined'])
def test_corrections_keep_prior_version_and_live_lesson_unchanged(app,outcome):
    manager,_,worker,lid,tid,*_=scenario(app);package(app,worker,tid);submit(worker,tid);identity=submission_id(app)
    decision(manager,identity,decision=outcome,feedback='Improve the presentation examples.')
    assert b'Improve the presentation examples.' in worker.get(f'/staff/lesson-tasks/{tid}').data
    draft(worker,tid)
    with app.app_context():
        rows=list(db.session.scalars(select(LessonSubmission).order_by(LessonSubmission.revision)))
        assert len(rows)==2 and rows[0].status==outcome and rows[1].status=='draft'
        assert db.session.get(Lesson,lid).body=='Published original'
    assert b'Awaiting administrator review' in submit(worker,tid).data


def test_reassignment_revokes_access_including_draft_assets_and_old_approval(app):
    manager,student,worker,lid,tid,member,sent,*_=scenario(app);files=package(app,worker,tid);submit(worker,tid);identity=submission_id(app)
    other=create(app,'New Content Officer');outsider=login(app,sent,other)
    assert outsider.get(f'/staff/lesson-tasks/{tid}').status_code==403
    post(manager,f'/admin/team/tasks/{tid}/reassign',page='/admin/team',member_id=other)
    assert worker.get(f'/staff/lesson-tasks/{tid}').status_code==403
    assert worker.get('/files/'+files['outline']).status_code in (302,403)
    assert b'assignment or published lesson changed' in decision(manager,identity).data
    assert outsider.get(f'/staff/lesson-tasks/{tid}').status_code==200
    with app.app_context():assert db.session.get(LessonSubmission,identity).status=='submitted'
    assert b'Create private draft' in outsider.get(f'/staff/lesson-tasks/{tid}').data
    draft(outsider,tid)
    with app.app_context():
        current=db.session.scalar(select(LessonSubmission).order_by(LessonSubmission.revision.desc()));assert current.revision==2 and current.preparer_id==other


def test_stale_published_lesson_blocks_approval_and_generic_task_completion(app):
    manager,_,worker,lid,tid,*_=scenario(app);package(app,worker,tid);submit(worker,tid);identity=submission_id(app)
    with app.app_context():db.session.get(Lesson,lid).body='Admin changed lesson';db.session.commit()
    assert b'published lesson changed' in decision(manager,identity).data
    assert post(worker,f'/staff/tasks/{tid}',status='completed',result='Bypass review').status_code==409
    with app.app_context():assert db.session.get(Lesson,lid).body=='Admin changed lesson'


def test_view_only_task_cannot_create_drafts_upload_or_schedule(app):
    _,_,worker,lid,tid,*_=scenario(app,'view');page=worker.get(f'/staff/lesson-tasks/{tid}');assert b'view only' in page.data
    assert post(worker,f'/staff/lesson-tasks/{tid}',action='begin').status_code==403
    assert post(worker,f'/staff/lesson-tasks/{tid}/schedule',starts_at='2027-01-01T12:00').status_code==403


def test_manual_scheduling_is_scoped_and_publishes_once(app):
    manager,student,worker,lid,tid,member,sent,cid,cohort,_=scenario(app,'schedule')
    fields=dict(starts_at='2027-01-15T12:00',timezone='America/Toronto',duration='120',zoom_mode='manual',join_url='https://zoom.us/j/123456789',cohort_id=cohort)
    result=post(worker,f'/staff/lesson-tasks/{tid}/schedule',**fields);assert result.status_code==200 and b'Class scheduled' in result.data
    post(worker,f'/staff/lesson-tasks/{tid}/schedule',**fields)
    with app.app_context():
        assert len(list(db.session.scalars(select(ClassSession))))==1
        assert db.session.get(LessonTask,tid).class_id
        assert db.session.get(StaffTask,tid).status=='completed'
        assert db.session.get(Lesson,lid).body=='Published original'


def test_direct_legacy_instructor_upload_cannot_bypass_assigned_lesson_review(app):
    _,_,worker,lid,tid,member,*_=scenario(app)
    with app.app_context():
        from app import TeachingGrant
        db.session.get(StaffMember,member).role='instructor';db.session.add(TeachingGrant(member_id=member,course_id=db.session.get(Lesson,lid).course_id));db.session.commit()
    result=post(worker,f'/staff/teaching/lessons/{lid}',title='Bypass',url='https://youtu.be/dQw4w9WgXcQ');assert result.status_code==403
    with app.app_context():assert not db.session.scalar(select(LessonMedia))


def test_review_email_whatsapp_have_private_action_link_and_are_deduplicated(app):
    import json
    manager,_,worker,lid,tid,*_=scenario(app);package(app,worker,tid);submit(worker,tid);identity=submission_id(app)
    service=app.extensions['integrations'];old_ready=service['ready'];old_config=service['config']
    values={'TWILIO_ACCOUNT_SID':'AC'+'1'*32,'TWILIO_AUTH_TOKEN':'fake-token','TWILIO_WHATSAPP_FROM':'whatsapp:+15555555555','TWILIO_REVIEW_TO':'+15555555556','TWILIO_REVIEW_TEMPLATE_SID':'HX'+'2'*32}
    service['ready']=lambda provider:True if provider in ('email','whatsapp') else old_ready(provider)
    service['config']=lambda provider,*a,**kw:{'SENDGRID_API_KEY':'fake-key','SENDGRID_FROM_EMAIL':'notifications@example.test'} if provider=='email' else values if provider=='whatsapp' else old_config(provider,*a,**kw)
    calls=[];app.config['LESSON_NOTICE_HTTP']=lambda *a,**kw:(calls.append((a,kw)) or {'sid':'SM'+'3'*32})
    with app.app_context():
        ids=list(db.session.scalars(select(LessonReviewNotice.id)))
        for notice in ids:
            app.extensions['lesson_review_notices']['process'](notice);app.extensions['lesson_review_notices']['process'](notice)
        assert all(n.status=='accepted' for n in db.session.scalars(select(LessonReviewNotice)))
    assert len(calls)==2
    mail=next(c[0][2] for c in calls if 'sendgrid' in c[0][0]);whatsapp=next(c[0][2] for c in calls if 'twilio' in c[0][0])
    assert '/admin/lesson-submissions/'+identity in mail['content'][0]['value']
    assert whatsapp['To']=='whatsapp:+15555555556'
    assert json.loads(whatsapp['ContentVariables'])['1']==task_number(tid)
    assert json.loads(whatsapp['ContentVariables'])['5'].endswith('/admin/lesson-submissions/'+identity)


def test_notification_missing_whatsapp_setup_does_not_block_submission_or_email(app):
    _,_,worker,lid,tid,*_=scenario(app);package(app,worker,tid);submit(worker,tid)
    service=app.extensions['integrations'];service['ready']=lambda provider:True;service['config']=lambda provider,*a,**kw:{'SENDGRID_API_KEY':'fake-key','SENDGRID_FROM_EMAIL':'notifications@example.test'} if provider=='email' else {}
    calls=[];app.config['LESSON_NOTICE_HTTP']=lambda *a,**kw:(calls.append(a) or {})
    with app.app_context():
        for identity in list(db.session.scalars(select(LessonReviewNotice.id))):app.extensions['lesson_review_notices']['process'](identity)
        assert db.session.scalar(select(LessonReviewNotice).where(LessonReviewNotice.channel=='email')).status=='accepted'
        assert db.session.scalar(select(LessonReviewNotice).where(LessonReviewNotice.channel=='whatsapp')).status=='awaiting_admin_number'
        assert db.session.scalar(select(LessonSubmission)).status=='submitted'
    assert len(calls)==1


def test_zoom_creation_task_is_idempotent_and_handles_uncertain_result(app):
    _,_,worker,lid,tid,*_=scenario(app,'schedule');service=app.extensions['integrations'];original=service['ready'];service['ready']=lambda provider:True if provider=='zoom' else original(provider)
    calls=[];app.config['ZOOM_REQUEST']=lambda *a,**kw:(calls.append(a) or {'id':123456789,'join_url':'https://zoom.us/j/123456789'})
    fields=dict(starts_at='2027-01-16T12:00',timezone='America/Toronto',duration='120',zoom_mode='api')
    result=post(worker,f'/staff/lesson-tasks/{tid}/schedule',**fields);assert b'Class scheduled' in result.data
    post(worker,f'/staff/lesson-tasks/{tid}/schedule',**fields);assert len(calls)==1
    with app.app_context():assert db.session.get(LessonTask,tid).scheduling_state=='ready'


def test_zoom_timeout_does_not_publish_or_create_duplicate_on_retry(app):
    _,_,worker,lid,tid,*_=scenario(app,'schedule');service=app.extensions['integrations'];original=service['ready'];service['ready']=lambda provider:True if provider=='zoom' else original(provider)
    calls=[]
    def timeout(*a,**kw):calls.append(a);raise TimeoutError()
    app.config['ZOOM_REQUEST']=timeout
    fields=dict(starts_at='2027-01-16T12:00',timezone='America/Toronto',duration='120',zoom_mode='api')
    post(worker,f'/staff/lesson-tasks/{tid}/schedule',**fields);post(worker,f'/staff/lesson-tasks/{tid}/schedule',**fields)
    assert len(calls)==1
    with app.app_context():
        assert db.session.get(LessonTask,tid).scheduling_state=='uncertain'
        assert not db.session.scalar(select(ClassSession))


def test_task_number_stays_the_same_after_reassignment(app):
    manager,_,worker,lid,tid,member,*_=scenario(app);number=task_number(tid);other=create(app,'Replacement')
    post(manager,f'/admin/team/tasks/{tid}/reassign',page='/admin/team',member_id=other)
    with app.app_context():assert task_number(db.session.get(StaffTask,tid))==number


def test_manual_resolution_cannot_race_an_inflight_zoom_request(app):
    _,_,worker,lid,tid,*_=scenario(app,'schedule')
    with app.app_context():
        from app import now
        assignment=db.session.get(LessonTask,tid);assignment.scheduling_state='creating';assignment.scheduling_started_at=now();db.session.commit()
    result=post(worker,f'/staff/lesson-tasks/{tid}/schedule',starts_at='2027-01-16T12:00',timezone='America/Toronto',duration='120',zoom_mode='manual',join_url='https://zoom.us/j/123456789',checked_zoom='yes')
    assert b'still processing' in result.data
    with app.app_context():assert not db.session.scalar(select(ClassSession))
