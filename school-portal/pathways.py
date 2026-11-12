"""Figma-based program discovery, level-specific classrooms and scheduled labs."""
import calendar
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import wraps
from zoneinfo import ZoneInfo
from flask import abort, flash, g, redirect, render_template, request, session, url_for, Response
from sqlalchemy import select
from werkzeug.utils import secure_filename
from cybersecurity_curriculum import STAGES as CYBER, CYBER_SLUGS
from analytics_curriculum import STAGES as ANALYTICS
from cloud_curriculum import STAGES as CLOUD, installments as cloud_installments

PROGRAMS={'cybersecurity':('Cybersecurity',CYBER),'data-analytics':('Data Analytics',ANALYTICS),'cloud-computing':('Cloud Computing',CLOUD)}

def cyber_starts(today):
    starts=[]
    for year in range(max(2027,today.year),max(2027,today.year)+2):
        for month in range(1,13):
            d=max(w[0] for w in calendar.monthcalendar(year,month))
            value=date(year,month,d)
            if value>=today:starts.append(value)
    return starts

def cyber_payment_stages(stages,start):
    if not start:return [dict(s,due=None,first_class=None) for s in stages]
    dates=[]
    for i in range(len(stages)):
        month=start.month-1+i;year=start.year+month//12;month=month%12+1
        dates.append(date(year,month,7))
    return [dict(s,due=dates[i].isoformat(),first_class=start.isoformat()) for i,s in enumerate(stages)]

def seed_pathways(connection,m):
    Course,Profile,Pub,Price,Policy,Lesson,Unit,Assessment,Lab=(m[k] for k in ('Course','ProgramProfile','CoursePublication','GlobalTuition','CoursePaymentPolicy','Lesson','CybersecurityUnit','Assessment','LabExercise'))
    for stage in CYBER:
        cid=connection.execute(select(Course.id).where(Course.slug==stage['slug'])).scalar()
        if not cid:
            cid=connection.execute(Course.__table__.insert().values(slug=stage['slug'],name='Cybersecurity · '+stage['name'],description=stage['summary'],skills='Network security · Threat detection · Ethical testing',registration_url='',resources_url='').returning(Course.id)).scalar_one()
        profile=connection.execute(select(Profile.__table__).where(Profile.course_id==cid)).mappings().first()
        if not profile:
            connection.execute(Profile.__table__.insert().values(course_id=cid,duration='8 weeks',schedule=stage['day']+' · 6–9 p.m. Eastern',curriculum='\n'.join(stage['titles']),tuition={}))
        elif not profile['duration']:
            connection.execute(Profile.__table__.update().where(Profile.course_id==cid).values(duration='8 weeks',schedule=stage['day']+' · 6–9 p.m. Eastern',curriculum='\n'.join(stage['titles'])))
        if not connection.execute(select(Pub.course_id).where(Pub.course_id==cid)).first():
            connection.execute(Pub.__table__.insert().values(course_id=cid,published=True,published_at=m['now'](),payment_links={}))
        if not connection.execute(select(Price.course_id).where(Price.course_id==cid)).first():
            connection.execute(Price.__table__.insert().values(course_id=cid,currency='USD',total=Decimal(stage['price']),installments=2,full_url='',installment_url=''))
        if not connection.execute(select(Policy.course_id).where(Policy.course_id==cid)).first():
            half=str(Decimal(stage['price'])/2)
            parts=[dict(label='First installment — enrollment included',amount=half,timing='7th of your starting month; immediately if already passed'),dict(label='Second installment',amount=half,timing='7th of the following month')]
            connection.execute(Policy.__table__.insert().values(course_id=cid,currency='USD',total=Decimal(stage['price']),checkout_url='',stages=parts,enabled=True))
        for number,title in enumerate(stage['titles'],1):
            if connection.execute(select(Unit.lesson_id).where(Unit.course_id==cid,Unit.number==number)).first():continue
            lid=connection.execute(Lesson.__table__.insert().values(course_id=cid,title=title,module=stage['name']+' · Week '+str(number),body='Learning focus: '+title+'.\nYour instructor will publish lesson notes, resources and practical instructions here.',position=number,published=False,available_at=m['now']()).returning(Lesson.id)).scalar_one()
            connection.execute(Unit.__table__.insert().values(course_id=cid,number=number,lesson_id=lid))
        for number,title in enumerate(stage['labs'],1):
            if connection.execute(select(Lab.id).where(Lab.course_id==cid,Lab.number==number)).first():continue
            aid=connection.execute(Assessment.__table__.insert().values(course_id=cid,title=title,kind='lab',instructions='Sample lab: '+title+'.\nUse only the instructor-provided training environment or supplied evidence.\nDocument your procedure, attach screenshots or a report, explain your findings, and recommend improvements.\nYour instructor will customize these instructions and set the lab schedule and submission deadline before publication.').returning(Assessment.id)).scalar_one()
            connection.execute(Lab.__table__.insert().values(course_id=cid,number=number,assessment_id=aid,published=False,revision=0))

    from cloud_curriculum import seed_cloud
    seed_cloud(connection,m)

def install(app,db,m,h):
    Course,Enrollment,Lesson,Lab,Assessment,Submission=(m[k] for k in ('Course','Enrollment','Lesson','LabExercise','Assessment','Submission'))
    def teacher():
        staff=getattr(g,'staff',None)
        return bool(session.get('admin') or (staff and staff.active and staff.verified_at and staff.role=='instructor'))
    def teacher_required(fn):
        @wraps(fn)
        def wrapped(*a,**kw):
            if not teacher():abort(403)
            return fn(*a,**kw)
        return wrapped
    def definition(program,level=None):
        if program not in PROGRAMS:abort(404)
        name,stages=PROGRAMS[program]
        stage=next((s for s in stages if s['key']==level),None) if level else None
        if level and not stage:abort(404)
        return name,stages,stage
    def course_for(stage):
        c=db.session.scalar(select(Course).where(Course.slug==stage['slug']))
        if not c or (not session.get('admin') and (not c.publication or not c.publication.published) and not (g.student and h['enrollment_for'](c.id))):abort(404)
        return c
    def eligible(course):
        return h['enrollment_for'](course.id) if g.student else None
    def visible_lab(lab,enrollment):
        return bool(enrollment and lab.published and (not lab.cohort_id or lab.cohort_id==enrollment.cohort_id))
    def labs_for(course,enrollment,managing=False):
        return [l for l in db.session.scalars(select(Lab).where(Lab.course_id==course.id).order_by(Lab.number)) if managing or visible_lab(l,enrollment)]
    def context(stage):
        c=course_for(stage)
        saved=db.session.scalar(select(Enrollment).where(Enrollment.student_id==g.student.id,Enrollment.course_id==c.id)) if g.student else None
        policy=app.extensions['program_link_plans']['policy'](c);price=db.session.get(m['GlobalTuition'],c.id)
        total=policy.total if policy else price.total if price else None
        currency=policy.currency if policy else price.currency if price else 'USD'
        parts=[]
        if stage['slug'].startswith('cloud-computing') and total is not None:
            amounts=[Decimal(p['amount']) for p in policy.stages] if policy and len(policy.stages)==3 else cloud_installments(total)
            offset=Decimal(0)
            for amount in amounts:
                parts.append(dict(amount=amount,offset=offset));offset+=amount
        return dict(stage,course=c,total=total,currency=currency,selected=saved,eligible=eligible(c),monthly_parts=parts)
    @app.get('/programs')
    def program_catalog():
        items=[]
        for slug,name in [('cybersecurity','Cybersecurity'),('devops','DevOps'),('data-analytics','Data Analytics'),('cloud-computing','Cloud Computing')]:
            c=db.session.scalar(select(Course).where(Course.slug==slug))
            live=bool(c and c.publication and c.publication.published)
            if not live and slug in PROGRAMS:
                live=bool(db.session.scalar(select(Course.id).join(m['CoursePublication']).where(m['CoursePublication'].published.is_(True),Course.slug.in_([s['slug'] for s in PROGRAMS[slug][1]]))))
            if c and live:items.append(c)
        return render_template('program_catalog.html',programs=items)
    @app.get('/programs/<program>')
    def program_levels(program):
        name,stages,_=definition(program)
        visible=[]
        for s in stages:
            c=db.session.scalar(select(Course).where(Course.slug==s['slug']))
            if c and (session.get('admin') or (c.publication and c.publication.published) or (g.student and eligible(c))):visible.append(context(s))
        if not visible:abort(404)
        return render_template('program_levels.html',program=program,program_name=name,stages=visible)
    @app.get('/programs/<program>/<level>')
    def program_level(program,level):
        name,stages,stage=definition(program,level);data=context(stage);course=data['course']
        unit=m[{'cybersecurity':'CybersecurityUnit','cloud-computing':'CloudUnit','data-analytics':'AnalyticsUnit'}[program]]
        units={u.number:u for u in db.session.scalars(select(unit).where(unit.course_id==course.id))}
        lessons=[]
        for index,title in enumerate(stage['titles'],1):
            number=stage.get('offset',0)+index;u=units.get(number)
            lesson=u.lesson if u else None
            sessions=app.extensions['devops']['lesson_context'](lesson,teacher())['lesson_classes'] if lesson and (data['eligible'] or teacher()) else []
            lessons.append(dict(number=number,week=index,title=title,lesson=lesson,sessions=sessions,released=bool(lesson and lesson.published and m['aware'](lesson.available_at)<=m['now']())))
        tab=request.args.get('tab','lessons')
        if tab not in ('lessons','resources','labs'):abort(404)
        if tab!='lessons' and not data['eligible'] and not teacher():abort(403)
        published=[l['lesson'] for l in lessons if l['released']]
        return render_template('program_level.html',program=program,program_name=name,levels=stages,data=data,lessons=lessons,tab=tab,managing=teacher(),resources=published,labs=labs_for(course,data['eligible'],teacher()),submissions={s.assessment_id:s for s in db.session.scalars(select(Submission).where(Submission.student_id==g.student.id))} if g.student else {})
    @app.route('/programs/<program>/<level>/enroll',methods=['GET','POST'])
    def level_enroll(program,level):
        name,_,stage=definition(program,level);data=context(stage);course=data['course']
        if not course.publication or not course.publication.published:abort(404)
        cohorts=[c for c in m['upcoming_cohorts']() if c.course_id==course.id]
        if request.method=='POST':
            try:
                cohort=db.get_or_404(m['Cohort'],int(request.form.get('cohort_id','0')))
                if cohort.course_id!=course.id or cohort.start_date not in m['available_start_dates'](course):raise ValueError('Choose a valid upcoming intake.')
                choice=request.form.get('plan','installments')
                if choice not in ('full','installments'):raise ValueError('Choose a payment option.')
                session['level_payment_choice']={'course_id':course.id,'plan':choice}
                session['application']={'course_id':course.id,'cohort_id':cohort.id}
                if not g.student:return redirect(url_for('register'))
                e=data['selected']
                if not e:
                    e=Enrollment(student_id=g.student.id,course_id=course.id,cohort_id=cohort.id);db.session.add(e);db.session.commit()
                return redirect(url_for('global_payment',enrollment_id=e.id))
            except ValueError as error:flash(str(error))
        return render_template('level_enroll.html',program=program,program_name=name,data=data,cohorts=cohorts)
    @app.get('/teaching/labs')
    @teacher_required
    def teaching_labs():
        courses=db.session.scalars(select(Course).where(Course.slug.in_([s['slug'] for _,stages in PROGRAMS.values() for s in stages]))).all()
        cid=request.args.get('course_id',type=int) or next((c.id for c in courses if c.slug=='cybersecurity'),0)
        if cid not in [c.id for c in courses]:abort(404)
        labs=db.session.scalars(select(Lab).where(Lab.course_id==cid).order_by(Lab.number)).all()
        return render_template('teaching_labs.html',courses=courses,selected_id=cid,labs=labs,assessments=db.session.scalars(select(Assessment).where(Assessment.course_id==cid,Assessment.kind=='lab',~Assessment.id.in_(select(Lab.assessment_id)))).all(),cohorts=db.session.scalars(select(m['Cohort']).where(m['Cohort'].course_id==cid).order_by(m['Cohort'].start_date.desc())).all(),admin_view='materials')
    def local_dt(value,zone):
        d=datetime.fromisoformat(value)
        if d.tzinfo:raise ValueError('Enter local time and select its time zone.')
        z=d.replace(tzinfo=ZoneInfo(zone))
        if z.astimezone(timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None)!=d or z.utcoffset()!=d.replace(tzinfo=ZoneInfo(zone),fold=1).utcoffset():raise ValueError('Choose an unambiguous local time.')
        return z.astimezone(timezone.utc)
    @app.post('/teaching/labs/save')
    @teacher_required
    def teaching_lab_save():
        try:
            cid=int(request.form.get('course_id','0'));course=db.get_or_404(Course,cid)
            if course.slug not in [s['slug'] for _,stages in PROGRAMS.values() for s in stages]:abort(404)
            lab=db.get_or_404(Lab,int(request.form['lab_id'])) if request.form.get('lab_id') else None
            if lab and lab.course_id!=cid:abort(403)
            cohort_id=int(request.form['cohort_id']) if request.form.get('cohort_id') else None
            if cohort_id and db.get_or_404(m['Cohort'],cohort_id).course_id!=cid:raise ValueError('Choose a cohort in the selected level.')
            title=m['field']('title',150);instructions=m['field']('instructions',10000)
            state=m['field']('state',20)
            if state not in ('draft','published'):raise ValueError('Choose draft or published.')
            from localization import TIMEZONES
            zone=m['field']('timezone',80)
            if zone not in TIMEZONES:raise ValueError('Choose a supported time zone.')
            starts=local_dt(m['field']('starts_at',40),zone);due=local_dt(m['field']('due_at',40),zone)
            duration=int(m['field']('duration',4))
            if not 15<=duration<=600 or due<starts:raise ValueError('Choose a valid duration and a deadline after the lab starts.')
            url=m['valid_url'](m['field']('join_url',3000,False),True)
            if state=='published' and not url:raise ValueError('Add the meeting link before publishing the scheduled lab.')
            if not lab:
                a=Assessment(course_id=cid,kind='lab',title=title,instructions=instructions);db.session.add(a);db.session.flush()
                number=max(db.session.scalars(select(Lab.number).where(Lab.course_id==cid)),default=0)+1
                lab=Lab(course_id=cid,number=number,assessment_id=a.id,assessment=a);db.session.add(lab)
            lab.assessment.title,lab.assessment.instructions,lab.assessment.due_at=title,instructions,due
            c=lab.scheduled_class or m['ClassSession'](course_id=cid)
            c.title='Lab · '+title;c.starts_at=starts
            if not c.joining:c.joining=m['ClassJoinLink'](url=url)
            else:c.joining.url=url
            db.session.add(c);db.session.flush();lab.class_id=c.id;lab.cohort_id=cohort_id;lab.published=state=='published';lab.revision=(lab.revision or 0)+1
            detail=db.session.get(m['ScheduledClass'],c.id) or m['ScheduledClass'](class_id=c.id)
            detail.cohort_id,detail.duration,detail.timezone,detail.published=cohort_id,duration,zone,lab.published
            detail.join_minutes=15;detail.revision=lab.revision;db.session.add(detail);db.session.flush()
            app.extensions['global_experience']['queue_class'](c,detail)
            db.session.add(m['StaffAudit'](actor='admin:1' if session.get('admin') else 'staff:'+str(g.staff.id),action='schedule_lab',detail=course.name+' · '+title+' · '+state))
            db.session.commit()
            if lab.published:app.extensions['integrations']['dispatch'](class_id=c.id)
            flash('Lab saved. Published labs appear in the matching level and class calendar; notification delivery is tracked in Integrations.')
        except (ValueError,KeyError) as error:db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'Complete the lab schedule fields.')
        return redirect(url_for('teaching_labs',course_id=request.form.get('course_id')))
    @app.route('/teaching/labs/<int:lab_id>/reviews',methods=['GET','POST'])
    @teacher_required
    def teaching_lab_reviews(lab_id):
        lab=db.get_or_404(Lab,lab_id)
        if request.method=='POST':
            try:
                item=db.get_or_404(Submission,int(request.form.get('submission_id','0')))
                if item.assessment_id!=lab.assessment_id:abort(403)
                score=int(request.form.get('score',''))
                if not 0<=score<=100:raise ValueError('Choose a score from 0 to 100.')
                item.score,item.feedback,item.reviewed_at=score,m['field']('feedback',3000,False),m['now']()
                h['notify'](item.student_id,'Your lab result is ready',lab.assessment.title+': '+str(score)+'/100')
                db.session.commit();flash('Lab grade saved and student notified.')
            except ValueError as error:db.session.rollback();flash(str(error))
        rows=db.session.scalars(select(Submission).where(Submission.assessment_id==lab.assessment_id)).all()
        return render_template('lab_reviews.html',lab=lab,submissions=rows,evidence=db.session.scalars(select(m['LabEvidence']).where(m['LabEvidence'].submission_id.in_([s.id for s in rows]))).all())
    @app.get('/labs/<int:lab_id>')
    @h['student_required']()
    def lab_page(lab_id):
        lab=db.get_or_404(Lab,lab_id);enrollment=eligible(db.get_or_404(Course,lab.course_id))
        if not visible_lab(lab,enrollment):abort(403)
        s=db.session.scalar(select(Submission).where(Submission.student_id==g.student.id,Submission.assessment_id==lab.assessment_id))
        evidence=db.session.scalars(select(m['LabEvidence']).where(m['LabEvidence'].submission_id==s.id)).all() if s else []
        return render_template('lab_page.html',lab=lab,submission=s,evidence=evidence)
    @app.before_request
    def lab_upload_limit():
        if request.endpoint=='lab_submit':request.max_content_length=12*1024*1024
    @app.post('/labs/<int:lab_id>/submit')
    @h['student_required']()
    def lab_submit(lab_id):
        lab=db.get_or_404(Lab,lab_id)
        if not visible_lab(lab,eligible(db.get_or_404(Course,lab.course_id))):abort(403)
        try:
            answer=m['field']('answer',10000);url=m['valid_url'](m['field']('url',3000,False),True)
            attachment=request.files.get('evidence');payload=None
            if attachment and attachment.filename:
                filename=secure_filename(attachment.filename)[:180];ext=filename.rsplit('.',1)[-1].lower();payload=attachment.read(10*1024*1024+1)
                if ext not in ('pdf','png','jpg','jpeg','txt','csv','ipynb') or not payload or len(payload)>10*1024*1024:raise ValueError('Choose a PDF, image, text, CSV or notebook file up to 10 MB.')
                if ext=='pdf' and not payload.startswith(b'%PDF'):raise ValueError('Choose a valid PDF report.')
                if ext in ('png','jpg','jpeg'):
                    from file_storage import validate_image
                    validate_image(payload,'image/png' if ext=='png' else 'image/jpeg')
            s=db.session.scalar(select(Submission).where(Submission.student_id==g.student.id,Submission.assessment_id==lab.assessment_id))
            if not s:s=Submission(student_id=g.student.id,assessment_id=lab.assessment_id);db.session.add(s)
            s.answer,s.url,s.submitted_at=answer,url,m['now']();s.score,s.feedback,s.reviewed_at=None,'',None;db.session.flush()
            if payload:
                path='lab-evidence/'+secrets.token_hex(24)+'/'+filename
                stored=app.config['FILE_STORAGE'].put(path,payload)
                db.session.add(m['LabEvidence'](submission_id=s.id,filename=filename,pathname=stored,mime='application/octet-stream'))
            db.session.commit();flash('Lab submitted for instructor review. Late submissions are marked against the published deadline.')
        except ValueError as error:db.session.rollback();flash(str(error))
        return redirect(url_for('lab_page',lab_id=lab.id))
    @app.get('/labs/evidence/<evidence_id>')
    def lab_evidence(evidence_id):
        e=db.get_or_404(m['LabEvidence'],evidence_id)
        if not teacher() and (not g.student or e.submission.student_id!=g.student.id or not eligible(e.submission.assessment.course)):abort(403)
        return Response(app.config['FILE_STORAGE'].get(e.pathname),mimetype='application/octet-stream',headers={'Content-Disposition':'attachment; filename="'+e.filename+'"','X-Content-Type-Options':'nosniff'})
    def assessment_visible(a):
        lab=db.session.scalar(select(Lab).where(Lab.assessment_id==a.id))
        return bool(g.student and lab and visible_lab(lab,eligible(a.course)))
    @app.context_processor
    def navigation():
        return dict(lab_deadline_input=lambda lab:m['aware'](lab.assessment.due_at).astimezone(ZoneInfo(app.extensions['global_experience']['class_detail'](lab.scheduled_class).timezone if lab.scheduled_class else 'America/Toronto')).strftime('%Y-%m-%dT%H:%M') if lab.assessment.due_at else '',pathway_url=lambda c:url_for('program_levels',program='cloud-computing' if c.slug.startswith('cloud-computing') else 'data-analytics' if c.slug.startswith('data-analytics') else 'cybersecurity') if c.slug.startswith(('data-analytics','cybersecurity','cloud-computing')) else url_for('course_page',slug=c.slug),can_manage_labs=teacher,lab_visible=lambda a:a.kind!='lab' or assessment_visible(a))
    return dict(teacher=teacher,visible_lab=visible_lab,labs_for=labs_for,assessment_visible=assessment_visible)
