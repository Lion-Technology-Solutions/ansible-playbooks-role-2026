"""Curriculum discovery is independent of paid content and schedule authorization."""
from flask import abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from devops_curriculum import MODULES


def install(app, db, m, guards):
    Course, Lesson, Unit = m['Course'], m['Lesson'], m['DevOpsUnit']
    Link, Activity, Class = m['LessonClass'], m['LessonActivity'], m['ClassSession']
    Enrollment, Asset = m['Enrollment'], m['Asset']

    def course():
        return db.session.scalar(select(Course).where(Course.slug == 'devops')) or abort(404)

    def link_activity(course_id, assessment_id=None, recording_id=None):
        if not request.form.get('return_lesson_id'):
            return
        lesson = db.get_or_404(Lesson, int(request.form['return_lesson_id']))
        if lesson.course_id != course_id:
            raise ValueError('The lesson and activity must belong to the same program.')
        db.session.add(Activity(lesson_id=lesson.id, assessment_id=assessment_id, recording_id=recording_id))

    def lesson_context(lesson, admin=False):
        classes = db.session.scalars(select(Class).join(Link, Link.class_id == Class.id).where(Link.lesson_id == lesson.id).order_by(Class.starts_at)).all()
        if not admin:
            classes = [c for c in classes if app.extensions['global_experience']['class_visible'](c, g.student)]
        assessments = db.session.scalars(select(m['Assessment']).join(Activity, Activity.assessment_id == m['Assessment'].id).where(Activity.lesson_id == lesson.id)).all()
        recordings = db.session.scalars(select(m['Recording']).join(Activity, Activity.recording_id == m['Recording'].id).where(Activity.lesson_id == lesson.id)).all()
        if not admin:
            recordings = [r for r in recordings if m['aware'](r.available_at) <= m['now']()]
        return dict(lesson_classes=classes, lesson_assessments=assessments, lesson_recordings=recordings)

    def notice_visible(notice, student):
        if notice.title != 'Class schedule updated':
            return True
        scope = db.session.get(m['ClassNoticeScope'], notice.id)
        if not scope:
            # Legacy notifications have no program identity. Never expose stale dates.
            return False
        scheduled = db.session.get(Class, scope.class_id)
        return bool(scheduled and app.extensions['global_experience']['class_visible'](scheduled, student))

    @app.get('/devops/lessons')
    def devops_catalog():
        if not g.student and not session.get('admin'):
            return redirect(url_for('login'))
        program = course()
        is_admin = bool(session.get('admin'))
        enrollment = guards['enrollment_for'](program.id) if g.student else None
        selected = db.session.scalar(select(Enrollment).where(Enrollment.student_id == g.student.id, Enrollment.course_id == program.id)) if g.student else None
        units = {u.number: u for u in db.session.scalars(select(Unit).where(Unit.course_id == program.id))}
        modules = []
        for month, (title, titles) in enumerate(MODULES, 1):
            lessons = []
            for offset, default_title in enumerate(titles, 1):
                number = (month - 1) * 8 + offset
                unit = units.get(number)
                lesson = unit.lesson if unit else None
                released = bool(lesson and lesson.published and m['aware'](lesson.available_at) <= m['now']())
                # Do not pass schedules or private draft fields to the unpaid catalogue.
                lessons.append(dict(number=number, title=default_title, lesson_id=lesson.id if lesson else None, released=released))
            modules.append(dict(month=month, title=title, weeks=f'{(month-1)*4+1}–{month*4}', lessons=lessons))
        return render_template('devops_catalog.html', modules=modules, program=program, eligible=bool(enrollment), selected=selected, managing=is_admin, admin_view='materials')

    @app.post('/admin/devops/lessons/<int:number>/open')
    @guards['admin_required']
    def devops_open(number):
        if not 1 <= number <= 48:
            abort(404)
        program = course()
        unit = db.session.get(Unit, (program.id, number))
        if not unit:
            module, titles = MODULES[(number - 1) // 8]
            title = titles[(number - 1) % 8]
            # Adopt a matching lesson if an administrator already created it.
            lesson = db.session.scalar(select(Lesson).where(Lesson.course_id == program.id, Lesson.title == title).order_by(Lesson.id))
            if not lesson:
                lesson = Lesson(course_id=program.id, title=title, module=f'Month {(number-1)//8+1} · {module}', position=number, published=False)
                db.session.add(lesson); db.session.flush()
            unit = Unit(course_id=program.id, number=number, lesson_id=lesson.id)
            db.session.add(unit)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                unit = db.session.get(Unit, (program.id, number))
                if not unit:
                    raise
        return redirect(url_for('devops_workspace', lesson_id=unit.lesson_id))

    @app.get('/admin/lessons/<int:lesson_id>/workspace')
    @guards['admin_required']
    def devops_workspace(lesson_id):
        lesson = db.get_or_404(Lesson, lesson_id)
        return render_template('devops_workspace.html', lesson=lesson, admin_view='materials',
            lesson_assets=db.session.scalars(select(Asset).where(Asset.ready.is_(True), Asset.archived.is_(False), Asset.purpose=='lesson').order_by(Asset.created_at.desc())).all(),
            **lesson_context(lesson, True))

    @app.context_processor
    def catalogue_navigation():
        return {'devops_catalog_url': url_for('devops_catalog')}

    app.extensions['devops'] = dict(link_activity=link_activity, lesson_context=lesson_context, notice_visible=notice_visible)
