"""Durable revision-bound reminders; only the delayed worker sends student notices."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from flask import g,request,session
from sqlalchemy import select,text
from sqlalchemy.exc import IntegrityError


def install(app,db,m):
    Job,Class,Detail,Enrollment,Event=(m[n] for n in ('ClassReminderJob','ClassSession','ScheduledClass','Enrollment','LifecycleEvent'))
    aware=m['aware']
    def clock():return app.config.get('CLASS_REMINDER_CLOCK',m['now'])()
    def plan(lesson,detail):
        if not detail.published or aware(lesson.starts_at)<=clock():return
        if db.session.scalar(select(Job.id).where(Job.class_id==lesson.id,Job.revision==detail.revision)):return
        db.session.add(Job(class_id=lesson.id,revision=detail.revision,due_at=aware(lesson.starts_at)-timedelta(minutes=20)))
        g.class_reminders_changed=True
    def arm():
        instant=clock()
        if db.engine.dialect.name=='postgresql':db.session.execute(text('SELECT pg_advisory_xact_lock(73592027)'))
        for lesson,detail in db.session.execute(select(Class,Detail).join(Detail,Detail.class_id==Class.id).where(Detail.published.is_(True),Class.starts_at>instant,Class.starts_at<=instant+timedelta(days=6))):plan(lesson,detail)
        try:db.session.commit()
        except IntegrityError:db.session.rollback()
        if app.testing and not app.config.get('CLASS_REMINDER_PUBLISH'):return 0
        from integration_jobs import publish_class_reminder
        publish=app.config.get('CLASS_REMINDER_PUBLISH',publish_class_reminder);count=0
        ids=list(db.session.scalars(select(Job.id).where(Job.status.in_(('pending','failed')),Job.attempts<5,Job.due_at<instant+timedelta(days=6)).order_by(Job.due_at)))
        for identity in ids:
            job=db.session.scalar(select(Job).where(Job.id==identity).with_for_update().execution_options(populate_existing=True))
            if job.status not in ('pending','failed'):db.session.commit();continue
            lesson=db.session.get(Class,job.class_id);detail=db.session.get(Detail,job.class_id)
            if not lesson or not detail or not detail.published or detail.revision!=job.revision or aware(lesson.starts_at)<=clock():job.status='cancelled';db.session.commit();continue
            job.attempts+=1
            try:
                publish({'kind':'class_reminder','id':job.id},max(timedelta(0),aware(job.due_at)-clock()));job.status='queued';count+=1
            except Exception:
                job.status='failed';app.logger.warning('Class reminder scheduling failed; durable job retained.')
            db.session.commit()
        return count
    def fire(identity):
        job=db.session.scalar(select(Job).where(Job.id==identity).with_for_update().execution_options(populate_existing=True))
        if not job or job.status in ('completed','cancelled'):return
        lesson=db.session.get(Class,job.class_id);detail=db.session.get(Detail,job.class_id);instant=clock()
        if not lesson or not detail or not detail.published or detail.revision!=job.revision or aware(lesson.starts_at)<=instant:
            job.status='cancelled';db.session.commit();return
        if instant<aware(job.due_at):raise RuntimeError('Reminder is not due yet.')
        notices=app.extensions['notifications']
        query=select(Enrollment).where(Enrollment.course_id==lesson.course_id,Enrollment.status=='approved',Enrollment.payment_status=='verified')
        if detail.cohort_id:query=query.where(Enrollment.cohort_id==detail.cohort_id)
        existing=set(db.session.scalars(select(Event.event_key).where(Event.kind=='class_reminder',Event.event_key.like(f'class-reminder:{lesson.id}:{job.revision}:%'))))
        for enrollment in db.session.scalars(query):
            student=enrollment.student
            for channel in ('email','whatsapp'):
                if channel=='whatsapp':
                    pref=db.session.get(m['ClassWhatsAppPreference'],student.id)
                    if not pref or not pref.enabled or not pref.consented_at:continue
                key=f'class-reminder:{lesson.id}:{job.revision}:{student.id}:{channel}'
                if key not in existing:
                    existing.add(key)
                    notices['enqueue'](student,'class_reminder',key,{'class_id':lesson.id,'revision':job.revision,'reminder_job':job.id},channel=channel)
        job.status='fired';db.session.commit()
        ids=list(db.session.scalars(select(Event.id).where(Event.kind=='class_reminder',Event.event_key.like(f'class-reminder:{lesson.id}:{job.revision}:%'))))
        def deliver(event_id):
            with app.app_context():
                try:notices['process'](event_id)
                except RuntimeError:pass
                row=db.session.get(Event,event_id)
                return row.status in ('accepted','cancelled','expired')
        if app.testing:results=[deliver(event_id) for event_id in ids]
        else:
            with ThreadPoolExecutor(max_workers=8) as executor:results=list(executor.map(deliver,ids))
        failed=not all(results)
        job=db.session.get(Job,identity);job.status='fired' if failed else 'completed';db.session.commit()
        if failed:raise RuntimeError('Class reminder delivery needs retry.')
    @app.after_request
    def schedule_committed(response):
        # First admin dashboard visit also arms existing classes after an upgrade.
        if response.status_code<400 and not session.get('role_preview') and (getattr(g,'class_reminders_changed',False) or (session.get('admin') and request.endpoint in ('admin','notification_admin','schedule_admin'))):
            g.class_reminders_changed=False
            try:arm()
            except Exception:db.session.rollback();app.logger.warning('Class reminder scheduler will retry on next administrator visit or daily maintenance.')
        return response
    return dict(plan=plan,arm=arm,fire=fire,clock=clock)
