import json
from datetime import timedelta
from sqlalchemy import select,func
from app import db,AssistantDraft,AssistantUsage,IntegrationAudit,Course,Lesson,LessonFile,ClassSession,ScheduledClass,Student,now
from test_portal import analytics,approve,csrf,register
from test_integrations import setup,configure,http_ok
from test_global_portal import schedule


def ready(app,manager):
    app.config['INTEGRATION_HTTP']=http_ok
    configure(app,manager,'openai',{'OPENAI_API_KEY':'PRIVATE-OPENAI-KEY','OPENAI_MODEL':'gpt-5.4-mini'})
    configure(app,manager,'openai',action='test')


def answer(app,reply='A useful answer.',action='answer',target=0,fields=None,seen=None):
    def respond(values,payload):
        if seen is not None:seen.append((values,payload))
        result=dict(reply=reply,action=action,target=target,fields=[dict(key=k,value=v) for k,v in (fields or {}).items()])
        return dict(status='completed',output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(result))])],usage=dict(input_tokens=100,output_tokens=50))
    app.config['OPENAI_REQUEST']=respond


def ask(client,admin=True,question='Help me',course=None):
    path='/admin/assistant' if admin else '/assistant'
    return client.post(path,data={'csrf':csrf(client,path),'question':question,'course_id':str(course) if course else ''})


def latest(app):
    with app.app_context():return db.session.scalar(select(AssistantDraft).order_by(AssistantDraft.created_at.desc())).id


def apply(app,manager,did,**extra):
    with app.app_context():
        d=db.session.get(AssistantDraft,did);fields=dict(d.payload);action=d.action
    endpoint='/admin/live-classes/save' if action.startswith('class_') else '/admin'
    if endpoint=='/admin':fields.update(action=action,return_view='programs')
    fields.update(csrf=csrf(manager,'/admin/assistant'),ai_draft_id=did,confirm_ai='yes',admin_password='test-admin-password');fields.update(extra)
    return manager.post(endpoint,data=fields,follow_redirects=True)


def test_setup_missing_permissions_masked_key_and_no_mutations_on_answer(app):
    manager,student=setup(app);calls=[]
    app.config['OPENAI_REQUEST']=lambda *a:calls.append(a)
    assert b'connect and test OpenAI' in ask(manager).data and not calls
    assert app.test_client().get('/assistant').status_code==302
    assert student.get('/admin/assistant').status_code==302
    assert manager.post('/admin/assistant',data={'question':'Test'}).status_code==400
    ready(app,manager);seen=[];answer(app,seen=seen,reply='<script>bad()</script>')
    result=ask(manager);assert result.status_code==200
    assert b'&lt;script&gt;bad()&lt;/script&gt;' in result.data and b'PRIVATE-OPENAI-KEY' not in result.data
    assert seen[0][1]['store'] is False and seen[0][1]['text']['format']['strict'] is True
    assert seen[0][1]['max_output_tokens']==2500 and seen[0][1]['model']=='gpt-5.4-mini'
    with app.app_context():assert not db.session.scalar(select(AssistantDraft));assert db.session.scalar(select(AssistantUsage)).output_tokens==50
    assert ask(manager,question='x'*3001).status_code==400


def test_student_context_is_own_record_published_no_secrets_or_join_links(app):
    manager,student=setup(app);ready(app,manager);course,cohort=analytics(app)
    approve(app,student,manager,course,cohort);schedule(manager,course,cohort)
    other=app.test_client();register(other,'HIDDEN-OTHER-STUDENT@example.com')
    with app.app_context():
        secret=Course(slug='private-program',name='HIDDEN-DRAFT-PROGRAM',description='private',skills='private');db.session.add(secret)
        from app import CoursePublication,ProgramProfile
        secret.publication=CoursePublication(published=False,payment_links={});secret.profile=ProgramProfile(tuition={});db.session.commit()
    seen=[];answer(app,seen=seen)
    assert ask(student,admin=False).status_code==200
    payload=json.loads(seen[0][1]['input']);raw=json.dumps(payload)
    assert 'HIDDEN-OTHER-STUDENT' not in raw and 'HIDDEN-DRAFT-PROGRAM' not in raw
    assert 'PRIVATE-OPENAI-KEY' not in raw and 'private-class' not in raw and 'password' not in raw and 'student_number' not in raw
    assert payload['portal_data']['my_account']['enrollments'][0]['payment_status']=='verified'
    answer(app,action='edit_course',target=course,fields={'name':'Attack'})
    assert b'No portal changes were made' in ask(student,admin=False).data
    with app.app_context():assert not db.session.scalar(select(AssistantDraft))


def test_new_program_draft_review_edits_confirm_atomic_apply_replay_and_owner(app):
    manager,student=setup(app);ready(app,manager)
    answer(app,action='create_course',fields={'name':'AI Cloud Program','description':'Hands-on course','skills':'Cloud','curriculum':'Foundations\nProject','publication_state':'published'})
    assert b'Review &amp; edit draft' in ask(manager,question='Launch this program').data
    did=latest(app)
    with app.app_context():assert not db.session.scalar(select(Course).where(Course.name=='AI Cloud Program'))
    review=manager.get('/admin/assistant/review/'+did);assert review.status_code==200 and b'Admin review required' in review.data
    assert student.get('/admin/assistant/review/'+did).status_code==302
    from test_portal import admin
    another=app.test_client();admin(another);assert another.get('/admin/assistant/review/'+did).status_code==403
    assert apply(app,manager,did,admin_password='wrong').status_code==403
    assert b'Review and confirm' in apply(app,manager,did,confirm_ai='').data
    assert b'Saved successfully' in apply(app,manager,did,name='Reviewed Cloud Program').data
    with app.app_context():
        c=db.session.scalar(select(Course).where(Course.name=='Reviewed Cloud Program'));assert c.publication.published and c.profile.curriculum=='Foundations\nProject'
        assert db.session.get(AssistantDraft,did).status=='applied'
        assert db.session.scalar(select(IntegrationAudit).where(IntegrationAudit.action=='create_course')).status=='admin_confirmed'
    assert b'applied or expired' in apply(app,manager,did).data
    with app.app_context():assert db.session.scalar(select(func.count()).select_from(Course).where(Course.name=='Reviewed Cloud Program'))==1


def test_program_profile_preserves_tuition_and_stale_expired_drafts_rejected(app):
    manager,student=setup(app);ready(app,manager);course,_=analytics(app)
    with app.app_context():c=db.session.get(Course,course);c.profile.tuition={'Other':'USD 90'};db.session.commit()
    answer(app,action='program_profile',target=course,fields={'curriculum':'Updated SQL\nPower BI'})
    ask(manager,course=course);did=latest(app)
    assert b'Saved successfully' in apply(app,manager,did).data
    with app.app_context():assert db.session.get(Course,course).profile.tuition=={'Other':'USD 90'}
    answer(app,action='edit_course',target=course,fields={'name':'Stale name'})
    ask(manager);did=latest(app)
    with app.app_context():c=db.session.get(Course,course);c.name='Changed by another admin';db.session.commit()
    assert manager.get('/admin/assistant/review/'+did).status_code==400
    assert b'target changed' in apply(app,manager,did).data
    ask(manager);did=latest(app)
    with app.app_context():db.session.get(AssistantDraft,did).created_at=now()-timedelta(hours=1);db.session.commit()
    assert b'expired' in apply(app,manager,did).data


def test_ai_class_update_uses_actual_schedule_zoom_and_delivery_handlers(app):
    manager,student=setup(app);ready(app,manager);course,cohort=analytics(app)
    approve(app,student,manager,course,cohort);schedule(manager,course,cohort)
    with app.app_context():cid=db.session.scalar(select(ClassSession.id))
    answer(app,action='class_update',target=cid,fields={'starts_at':'2030-06-20T18:30','timezone':'America/Toronto','title':'AI reviewed SQL class','state':'published'})
    assert ask(manager,course=course).status_code==200;did=latest(app)
    review=manager.get('/admin/assistant/review/'+did);assert review.status_code==200 and b'Current value' in review.data
    assert b'Class saved' in apply(app,manager,did).data
    from app import ClassDelivery,Notification
    with app.app_context():
        lesson=db.session.get(ClassSession,cid);assert lesson.title=='AI reviewed SQL class' and lesson.starts_at.hour==22 and lesson.starts_at.minute==30
        assert db.session.get(ScheduledClass,cid).revision==2
        assert db.session.scalar(select(ClassDelivery).where(ClassDelivery.revision==2))
        assert db.session.scalar(select(Notification).where(Notification.student_id==db.session.scalar(select(Student.id))))
    # Invalid dates still pass through normal validation and do not consume a draft.
    ask(manager);did=latest(app)
    assert b'local time' in apply(app,manager,did,starts_at='2030-06-20T18:30+01:00').data
    with app.app_context():assert db.session.get(AssistantDraft,did).status=='pending'


def test_invalid_model_fields_forged_target_and_provider_failure_are_redacted(app):
    manager,student=setup(app);ready(app,manager);course,_=analytics(app)
    answer(app,action='edit_course',target=course,fields={'payment_status':'verified'})
    assert b'No portal changes were made' in ask(manager).data
    answer(app,action='edit_course',target=course,fields={'name':'Valid name'})
    ask(manager);did=latest(app)
    assert apply(app,manager,did,course_id='999').status_code==400
    def failure(*a):raise RuntimeError('PRIVATE-OPENAI-KEY provider error with private data')
    app.config['OPENAI_REQUEST']=failure
    result=ask(manager);assert b'PRIVATE-OPENAI-KEY' not in result.data and b'No portal changes were made' in result.data
    with app.app_context():assert db.session.scalar(select(AssistantUsage).order_by(AssistantUsage.id.desc())).status=='failed'


def test_persistent_hourly_daily_limits_and_paused_configuration(app):
    manager,student=setup(app);ready(app,manager);answer(app)
    ask(student,admin=False)
    with app.app_context():
        principal=db.session.scalar(select(AssistantUsage.principal))
        db.session.add_all([AssistantUsage(principal=principal) for _ in range(19)]);db.session.commit()
    assert ask(student,admin=False).status_code==429
    configure(app,manager,'openai',action='pause')
    assert b'connect and test OpenAI' in ask(manager).data


def test_lesson_updates_keep_existing_files_and_invalid_edits_roll_back(app):
    manager,student=setup(app);ready(app,manager);course,_=analytics(app)
    answer(app,action='create_lesson',target=course,fields={'title':'SQL practice','body':'Original notes','position':'2','publication_state':'published'})
    ask(manager);did=latest(app);assert b'Saved successfully' in apply(app,manager,did).data
    from app import Asset
    with app.app_context():
        lesson=db.session.scalar(select(Lesson).where(Lesson.title=='SQL practice'));lid=lesson.id
        asset=Asset(id='a'*32,title='Practice data',filename='practice.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',size=25,purpose='lesson',ready=True);db.session.add(asset)
        db.session.add(LessonFile(lesson_id=lid,asset_id=asset.id));db.session.commit()
    answer(app,action='edit_lesson',target=lid,fields={'body':'Updated practice instructions'})
    ask(manager);did=latest(app)
    assert b'lesson position' in apply(app,manager,did,position='0').data
    with app.app_context():assert db.session.get(AssistantDraft,did).status=='pending' and db.session.get(Lesson,lid).body=='Original notes'
    assert b'Saved successfully' in apply(app,manager,did).data
    with app.app_context():
        lesson=db.session.get(Lesson,lid);assert lesson.body=='Updated practice instructions' and len(lesson.attachments)==1


def test_new_start_dates_public_announcements_and_connected_zoom_scheduling(app):
    manager,student=setup(app);ready(app,manager);course,_=analytics(app)
    answer(app,action='cohort',target=course,fields={'start_date':'2031-05-10','label':'Weekend May cohort'})
    ask(manager);assert b'Saved successfully' in apply(app,manager,latest(app)).data
    from app import Cohort,PublicUpdate
    with app.app_context():cohort=db.session.scalar(select(Cohort).where(Cohort.label=='Weekend May cohort')).id
    answer(app,action='create_update',fields={'title':'New May cohort','summary':'Enrollment is open','body':'Join our weekend program','publication_state':'published'})
    ask(manager);assert b'Saved successfully' in apply(app,manager,latest(app)).data
    assert b'New May cohort' in student.get('/updates').data
    configure(app,manager,'zoom');configure(app,manager,'zoom',action='test')
    calls=[]
    app.config['ZOOM_REQUEST']=lambda method,path,payload:calls.append((method,path,payload)) or {'id':'zoom-123','join_url':'https://zoom.us/j/private-ai-meeting'}
    answer(app,action='class_create',target=course,fields={'title':'May SQL workshop','starts_at':'2031-05-10T18:00','timezone':'America/Toronto','cohort_id':str(cohort),'state':'published','zoom_mode':'api'})
    ask(manager);did=latest(app);assert not calls
    assert b'Class saved' in apply(app,manager,did).data
    assert len(calls)==1 and calls[0][0]=='POST'
    with app.app_context():
        lesson=db.session.scalar(select(ClassSession).where(ClassSession.title=='May SQL workshop'));cid=lesson.id
        assert db.session.get(ScheduledClass,cid).meeting_id=='zoom-123'
    answer(app,action='class_update',target=cid,fields={'starts_at':'2031-05-11T19:00'})
    ask(manager);assert b'Class saved' in apply(app,manager,latest(app)).data
    assert len(calls)==2 and calls[-1][0]=='PATCH' and calls[-1][1]=='meetings/zoom-123'


def test_provider_failures_distinguish_quota_rate_permission_and_invalid_request(app):
    import io
    from urllib.error import HTTPError
    manager,student=setup(app);ready(app,manager)
    cases=[(429,'insufficient_quota','quota_exhausted',b'no available API quota'),(429,'rate_limit_exceeded','rate_limited',b'rate-limiting'),(403,'permission_denied','permission_denied',b'Responses write access'),(400,'invalid_json_schema','invalid_request',b'response format'),(401,'invalid_api_key','authentication',b'HTTP 401')]
    for status,code,category,expected in cases:
        def fail(*args):raise HTTPError('https://api.openai.com/v1/responses',status,'secret response',{},io.BytesIO(json.dumps({'error':{'code':code,'message':'PRIVATE-OPENAI-KEY and confidential prompt'}}).encode()))
        app.config['OPENAI_REQUEST']=fail
        page=ask(manager);assert expected in page.data and b'PRIVATE-OPENAI-KEY' not in page.data and b'confidential prompt' not in page.data
        with app.app_context():assert db.session.scalar(select(IntegrationAudit).order_by(IntegrationAudit.id.desc())).status==category
    with app.app_context():assert not db.session.scalar(select(AssistantDraft))


def test_incomplete_refused_and_timeout_do_not_apply_partial_outputs(app):
    manager,student=setup(app);ready(app,manager)
    app.config['OPENAI_REQUEST']=lambda *args:{'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'},'output':[{'type':'message','content':[{'type':'output_text','text':'{"action":"create_course"'}]}]}
    assert b'response token limit' in ask(manager).data
    app.config['OPENAI_REQUEST']=lambda *args:{'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'private text not shown'}]}]}
    page=ask(manager);assert b'declined this request' in page.data and b'private text not shown' not in page.data
    def timeout(*args):raise TimeoutError('private transport details')
    app.config['OPENAI_REQUEST']=timeout
    page=ask(manager);assert b'timed out' in page.data and b'private transport details' not in page.data
    with app.app_context():assert not db.session.scalar(select(AssistantDraft))


def test_actual_generation_probe_uses_schema_without_student_context_and_requires_admin_password(app):
    manager,student=setup(app);ready(app,manager);seen=[];answer(app,seen=seen)
    assert configure(app,manager,'openai',action='test_generation',admin_password='wrong').status_code==403
    result=configure(app,manager,'openai',action='test_generation');assert b'AI response test passed' in result.data
    assert len(seen)==1 and json.loads(seen[0][1]['input'])=={'question':'Reply with Ready.'}
    assert seen[0][1]['text']['format']['strict'] and seen[0][1]['reasoning']=={'effort':'none'}
    with app.app_context():assert not db.session.scalar(select(AssistantDraft))
    import io
    from urllib.error import HTTPError
    def fail(*args):raise HTTPError('https://api.openai.com/v1/responses',429,'',{},io.BytesIO(b'{"error":{"code":"insufficient_quota"}}'))
    app.config['OPENAI_REQUEST']=fail
    result=configure(app,manager,'openai',action='test_generation');assert b'no available API quota' in result.data



def test_current_quota_codes_and_unknown_429_are_not_mislabeled_as_temporary_rate_limits():
    import io
    from urllib.error import HTTPError
    from portal_assistant import request_failure
    for code,category in [('credit_balance_exhausted','quota_exhausted'),('project_spend_limit_exceeded','spend_limit'),('organization_spend_limit_exceeded','spend_limit'),('organization_usage_limit_exceeded','usage_limit'),('slow_down','rate_limited'),('future_unknown_code','limit_unknown')]:
        error=HTTPError('https://api.openai.com/v1/responses',429,'',{},io.BytesIO(json.dumps({'error':{'code':code}}).encode()))
        assert request_failure(error).category==category
    assert request_failure(HTTPError('https://api.openai.com/v1/responses',429,'',{},io.BytesIO(b'not-json'))).category=='limit_unknown'
