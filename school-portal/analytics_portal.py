"""Three separately enrolled analytics stages and their public weekly catalogue."""
from decimal import Decimal
from flask import abort, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from analytics_curriculum import STAGES, ANALYTICS_SLUGS


def seed_stages(connection, m):
    """Add stages under the existing bootstrap lock; never rewrite saved student plans."""
    Course, Profile, Publication, Price, Policy = (m[k] for k in (
        'Course','ProgramProfile','CoursePublication','GlobalTuition','CoursePaymentPolicy'))
    for stage in STAGES:
        cid = connection.execute(select(Course.id).where(Course.slug == stage['slug'])).scalar()
        if not cid:
            cid = connection.execute(Course.__table__.insert().values(
                slug=stage['slug'], name='Data Analytics · '+stage['name'],
                description=stage['summary'], skills='SQL · Power BI · Python · Analytics',
                registration_url='', resources_url='').returning(Course.id)).scalar_one()
        profile = connection.execute(select(Profile.__table__).where(Profile.course_id == cid)).mappings().first()
        if not profile:
            connection.execute(Profile.__table__.insert().values(course_id=cid, duration=str(stage['weeks'])+' weeks',
                curriculum='\n'.join(stage['titles']), schedule='', tuition={}))
        elif stage['key'] == 'foundation':
            if profile['duration'] in ('','3 months'):
                connection.execute(Profile.__table__.update().where(Profile.course_id == cid).values(duration='12 weeks'))
            if profile['curriculum'] in ('','Excel\nSQL\nPython\nPower BI'):
                connection.execute(Profile.__table__.update().where(Profile.course_id == cid).values(curriculum='\n'.join(stage['titles'])))
        if not connection.execute(select(Publication.course_id).where(Publication.course_id == cid)).first():
            connection.execute(Publication.__table__.insert().values(course_id=cid, published=True, published_at=m['now'](), payment_links={}))
        if stage['key'] == 'foundation':
            continue
        if not connection.execute(select(Price.course_id).where(Price.course_id == cid)).first():
            connection.execute(Price.__table__.insert().values(course_id=cid, currency='USD', total=Decimal(stage['price']), installments=1, full_url='', installment_url=''))
        if not connection.execute(select(Policy.course_id).where(Policy.course_id == cid)).first():
            connection.execute(Policy.__table__.insert().values(course_id=cid, currency='USD', total=Decimal(stage['price']), checkout_url='', enabled=True,
                stages=[dict(label=stage['name']+' tuition — unlocks stage access', amount=stage['price'], timing='Due immediately on enrollment')]))


def install(app, db, m, h):
    Course, Enrollment, Lesson, Unit = (m[k] for k in ('Course','Enrollment','Lesson','AnalyticsUnit'))

    @app.get('/data-analytics/lessons')
    def analytics_catalog():
        if not g.student and not session.get('admin'):
            return redirect(url_for('login'))
        courses = {c.slug:c for c in db.session.scalars(select(Course).where(Course.slug.in_(ANALYTICS_SLUGS)))}
        enrollments = {e.course_id:e for e in db.session.scalars(select(Enrollment).where(Enrollment.student_id==g.student.id))} if g.student else {}
        units = {(u.course_id,u.number):u for u in db.session.scalars(select(Unit))}
        stages = []
        for definition in STAGES:
            course = courses.get(definition['slug'])
            if not course or (not session.get('admin') and not course.publication.published):
                continue
            selected = enrollments.get(course.id)
            eligible = bool(selected and selected.status=='approved' and selected.payment_status=='verified')
            # Resolve actual policy pricing, including legacy enrollment snapshots, without exposing USD to students.
            saved = (db.session.get(m['EnrollmentPaymentSchedule'],selected.id) or db.session.get(m['BillingSelection'],selected.id)) if selected else None
            policy = app.extensions['program_link_plans']['policy'](course)
            price = db.session.get(m['GlobalTuition'],course.id)
            total = saved.total if saved else policy.total if policy else price.total if price else None
            currency = saved.currency if saved else policy.currency if policy else price.currency if price else 'USD'
            lessons = []
            for index,title in enumerate(definition['titles'],1):
                number = definition['offset']+index
                unit = units.get((course.id,number))
                lesson = unit.lesson if unit else None
                lessons.append(dict(number=number,title=title,lesson_id=lesson.id if lesson else None,
                    released=bool(lesson and lesson.published and m['aware'](lesson.available_at)<=m['now']())))
            stages.append(dict(definition,course=course,selected=selected,eligible=eligible,total=total,currency=currency,lessons=lessons))
        active=request.args.get('stage','all' if session.get('admin') else 'foundation')
        if active not in ('all',*(stage['key'] for stage in stages)):abort(404)
        return render_template('analytics_catalog.html', stages=stages, active_stage=active, managing=bool(session.get('admin')), admin_view='materials')

    @app.post('/admin/data-analytics/<stage_key>/lessons/<int:number>/open')
    @h['admin_required']
    def analytics_open(stage_key,number):
        stage = next((s for s in STAGES if s['key']==stage_key), None)
        if not stage or not stage['offset'] < number <= stage['offset']+stage['weeks']:
            abort(404)
        course = db.session.scalar(select(Course).where(Course.slug==stage['slug']))
        if not course:
            abort(404)
        unit = db.session.get(Unit,(course.id,number))
        if not unit:
            index = number-stage['offset']-1
            title = stage['titles'][index]
            lesson = db.session.scalar(select(Lesson).where(Lesson.course_id==course.id,Lesson.title==title).order_by(Lesson.id))
            if not lesson:
                from analytics_lesson_notes import lesson_notes
                lesson = Lesson(course_id=course.id,title=title,module=stage['name']+' · Week '+str(number),position=number,
                    published=False,body=lesson_notes(stage['key'],index))
                db.session.add(lesson);db.session.flush()
            unit = Unit(course_id=course.id,number=number,lesson_id=lesson.id)
            db.session.add(unit)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback();unit=db.session.get(Unit,(course.id,number))
                if not unit:
                    raise
        return redirect(url_for('devops_workspace',lesson_id=unit.lesson_id))

    @app.context_processor
    def navigation():
        return dict(
            curriculum_url=lambda course: url_for('analytics_catalog') if course.slug in ANALYTICS_SLUGS else url_for('program_levels',program='cloud-computing') if course.slug.startswith('cloud-computing') else url_for('program_levels',program='cybersecurity') if course.slug.startswith('cybersecurity') else url_for('program_levels',program='devops'),
            curriculum_label=lambda course: 'Data Analytics curriculum' if course.slug in ANALYTICS_SLUGS else 'Cloud Computing curriculum' if course.slug.startswith('cloud-computing') else 'Cybersecurity curriculum' if course.slug.startswith('cybersecurity') else 'DevOps curriculum')
