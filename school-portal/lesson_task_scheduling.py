"""Immediate publication for a specifically assigned class-scheduling task."""
from datetime import datetime,timezone,timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo
from flask import abort,flash,g,redirect,request,url_for
from sqlalchemy import select
from lesson_teaching import media_url
from lesson_tasks import task_number
from providers import zoom_request,setting


def install(app,db,m):
    @app.post('/staff/lesson-tasks/<int:tid>/schedule')
    def lesson_task_schedule(tid):
        task,assignment,lesson=app.extensions['lesson_tasks']['access'](tid)
        if not g.staff or task.member_id!=g.staff.id or assignment.action!='schedule':abort(403)
        if assignment.class_id:
            flash('This task already scheduled its class. Ask an administrator to make further changes.');return redirect(url_for('staff_lesson_task',tid=tid))
        member_id=g.staff.id;course_id=lesson.course_id;started=False
        try:
            zone=request.form.get('timezone','America/Toronto');duration=int(request.form.get('duration','120'))
            if zone not in m['TIMEZONES'] or not 15<=duration<=600:raise ValueError('Choose a valid timezone and a duration of 15–600 minutes.')
            local=datetime.fromisoformat(request.form.get('starts_at',''))
            if local.tzinfo:raise ValueError('Enter local time and select its timezone.')
            zoned=local.replace(tzinfo=ZoneInfo(zone))
            if zoned.astimezone(timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None)!=local or zoned.utcoffset()!=local.replace(tzinfo=ZoneInfo(zone),fold=1).utcoffset():raise ValueError('Choose an unambiguous local class time.')
            if zoned.astimezone(timezone.utc)<=m['now']():raise ValueError('Choose a future class time.')
            cohort=request.form.get('cohort_id',type=int)
            if cohort and db.get_or_404(m['Cohort'],cohort).course_id!=lesson.course_id:abort(403)
            mode=request.form.get('zoom_mode','manual');meeting_id=None
            if mode not in ('manual','api'):abort(400)
            if mode=='api':
                if assignment.scheduling_state in ('creating','uncertain'):raise ValueError('A Zoom request may already have created a meeting. Check Zoom and supply its existing meeting link; do not create another automatically.')
                if not app.extensions['integrations']['ready']('zoom'):raise ValueError('Connect Zoom in Integrations first, or use an existing Zoom meeting link.')
                assignment.scheduling_state='creating';assignment.scheduling_started_at=m['now']();db.session.commit();started=True
                result=app.config.get('ZOOM_REQUEST',zoom_request)('POST','users/'+quote(setting('ZOOM_HOST_USER'),safe='')+'/meetings',{'topic':lesson.title,'type':2,'start_time':zoned.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),'duration':duration,'timezone':zone,'settings':{'waiting_room':True,'join_before_host':False}})
                kind,link=media_url(result.get('join_url',''));meeting_id=str(result['id'])
                if kind!='zoom' or '/j/' not in link or len(meeting_id)>100:raise ValueError('Zoom did not return usable meeting details.')
            else:
                if assignment.scheduling_state=='creating' and (not assignment.scheduling_started_at or m['now']()-m['aware'](assignment.scheduling_started_at)<timedelta(minutes=5)):raise ValueError('The Zoom scheduling request is still processing. Wait for its result before attaching a meeting link.')
                if assignment.scheduling_state in ('creating','uncertain') and request.form.get('checked_zoom')!='yes':raise ValueError('Confirm you checked Zoom for an existing meeting before saving its link.')
                kind,link=media_url(request.form.get('join_url',''))
                if kind!='zoom' or '/j/' not in link:raise ValueError('Use a Zoom meeting link for a live class.')
            # Recheck ownership after the external provider call.
            db.session.expire_all();task,assignment,lesson=app.extensions['lesson_tasks']['access'](tid)
            if task.member_id!=member_id or assignment.class_id or lesson.course_id!=course_id:abort(409,'The assignment changed while Zoom was being contacted. Check Zoom before retrying.')
            classroom=m['ClassSession'](course_id=lesson.course_id,title=lesson.title,starts_at=zoned.astimezone(timezone.utc),registration_url='');classroom.joining=m['ClassJoinLink'](url=link);db.session.add(classroom);db.session.flush()
            detail=m['ScheduledClass'](class_id=classroom.id,cohort_id=cohort,duration=duration,timezone=zone,instructor=g.staff.name,published=True,revision=1,meeting_id=meeting_id,join_minutes=15);db.session.add(detail);db.session.add(m['LessonClass'](class_id=classroom.id,lesson_id=lesson.id));db.session.flush()
            assignment.class_id=classroom.id;assignment.scheduling_state='ready';task.status='completed';task.result='Class scheduled and published.';task.updated_at=m['now']()
            app.extensions['global_experience']['queue_class'](classroom,detail)
            db.session.add(m['StaffAudit'](actor='staff:'+str(member_id),member_id=member_id,action='lesson_task_class_published',detail=task_number(task)+'; class '+str(classroom.id)));db.session.commit()
            try:app.extensions['integrations']['dispatch'](class_id=classroom.id)
            except Exception:db.session.rollback();app.logger.warning('Published lesson-task class has outstanding notification delivery.')
            flash('Class published. Eligible students can see its schedule under the existing enrollment rules.')
        except Exception as error:
            from werkzeug.exceptions import HTTPException
            db.session.rollback()
            if started:
                row=db.session.get(m['LessonTask'],tid)
                if row and not row.class_id:row.scheduling_state='uncertain';db.session.commit()
            if isinstance(error,HTTPException):raise
            flash(str(error) if isinstance(error,ValueError) else 'The provider did not confirm scheduling. Check Zoom for an existing meeting before retrying.')
        return redirect(url_for('staff_lesson_task',tid=tid))
