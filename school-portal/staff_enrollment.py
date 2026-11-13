"""Assigned-record enrollment preparation; financial and access authority stays with admin."""
import hashlib,json,secrets
from datetime import date
from zoneinfo import ZoneInfo
from flask import abort,flash,g,redirect,render_template,request,session,url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from admin_enrollment import amount,installments

FIELDS=('course_id','student_kind','cohort_id','existing_start','agreed_total','currency','paid_total','installment_amount','next_due','frequency','notes')


def define(db,now):
    class StaffEnrollmentSubmission(db.Model):
        __tablename__='staff_enrollment_submissions'
        id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
        task_id=db.Column(db.Integer,db.ForeignKey('staff_tasks.id'),nullable=False,index=True)
        student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False,index=True)
        preparer_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),nullable=False)
        enrollment_id=db.Column(db.Integer,db.ForeignKey('portal_enrollments.id'))
        revision=db.Column(db.Integer,nullable=False)
        status=db.Column(db.String(24),nullable=False,default='draft',index=True)
        payload=db.Column(db.JSON,nullable=False,default=dict)
        financial_version=db.Column(db.String(64))
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
        updated_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
        submitted_at=db.Column(db.DateTime(timezone=True))
        reviewed_at=db.Column(db.DateTime(timezone=True))
        reviewer=db.Column(db.String(100))
        feedback=db.Column(db.Text,nullable=False,default='')
        approved_values=db.Column(db.JSON)
        __table_args__=(db.UniqueConstraint('task_id','revision'),)
    return dict(StaffEnrollmentSubmission=StaffEnrollmentSubmission)


def install(app,db,m,h):
    Submission,Task,Link,Member,Enrollment=(m[k] for k in ('StaffEnrollmentSubmission','StaffTask','TaskRecord','StaffMember','Enrollment'))
    preview=app.extensions['admin_enrollment']['preview'];now=m['now']
    def latest(task_id):return db.session.scalar(select(Submission).where(Submission.task_id==task_id).order_by(Submission.revision.desc()).limit(1))
    def active(row):
        task=db.session.get(Task,row.task_id);member=db.session.get(Member,row.preparer_id);link=db.session.get(Link,row.task_id)
        return bool(task and member and link and member.active and member.verified_at and task.member_id==row.preparer_id and link.student_id==row.student_id and (not link.enrollment_id or not row.enrollment_id or link.enrollment_id==row.enrollment_id) and latest(task.id).id==row.id)
    def pending(student,course_id=None):
        rows=db.session.scalars(select(Submission).where(Submission.student_id==student.id,Submission.status=='submitted'))
        return any(active(row) and (course_id is None or row.payload.get('course_id')==str(course_id)) for row in rows)
    def requires_review(student,course_id):
        # A returned/reassigned packet still needs a final administrator decision.
        # Unlike retention protection, this gate must not disappear on reassignment.
        return any(row.payload.get('course_id')==str(course_id) for row in db.session.scalars(select(Submission).where(Submission.student_id==student.id,Submission.status!='draft')))
    def version(row):return hashlib.sha256(json.dumps({'id':row.id,'revision':row.revision,'status':row.status,'payload':row.payload,'updated':str(row.updated_at)},sort_keys=True).encode()).hexdigest()
    def target(student,values,link):
        if link.enrollment_id:
            enrollment=db.session.scalar(select(Enrollment).where(Enrollment.id==link.enrollment_id).with_for_update())
            if not enrollment:abort(404)
            if enrollment.student_id!=student.id or str(enrollment.course_id)!=values.get('course_id'):raise ValueError('This assignment is restricted to its existing program. Ask an administrator to assign another program.')
            return enrollment
        return db.session.scalar(select(Enrollment).where(Enrollment.student_id==student.id,Enrollment.course_id==int(values['course_id'])).with_for_update())
    def audit(row,action):db.session.add(m['StaffAudit'](actor='admin:1' if session.get('admin') else 'staff:'+str(row.preparer_id),member_id=row.preparer_id,action=action,detail=f'Submission {row.id}; task {row.task_id}; revision {row.revision}'))
    def validate(student,values,link):
        try:
            cid=int(values['course_id']);course=db.session.get(m['Course'],cid) if 0<cid<2147483648 else None
        except (ValueError,TypeError):course=None
        if not course or not course.publication or not course.publication.published:raise ValueError('Choose a published program.')
        if values['student_kind'] not in ('new','existing'):raise ValueError('Select new or existing student.')
        if values['cohort_id'] and values['existing_start']:raise ValueError('Select a cohort or a historical start date, not both.')
        if values['cohort_id']:
            try:
                cid=int(values['cohort_id']);cohort=db.session.get(m['Cohort'],cid) if 0<cid<2147483648 else None
            except ValueError:cohort=None
            if not cohort or cohort.course_id!=course.id:raise ValueError('Choose a cohort belonging to this program.')
        elif values['existing_start']:
            if values['student_kind']!='existing' or date.fromisoformat(values['existing_start'])>now().astimezone(ZoneInfo('America/Toronto')).date():raise ValueError('Historical start dates are only for existing students and cannot be in the future.')
        else:raise ValueError('Choose a cohort or enter the existing student’s historical start date.')
        total,paid=amount(values['agreed_total']),amount(values['paid_total'])
        if paid>total:raise ValueError('Reported paid-to-date cannot exceed agreed tuition.')
        if values['currency'] not in m['CURRENCIES']:raise ValueError('Choose a supported currency.')
        if total>paid:installments(total-paid,amount(values['installment_amount']),date.fromisoformat(values['next_due']),values['frequency'])
        if not values['notes']:raise ValueError('Record the student contact outcome and the source of reported payment information.')
        enrollment=target(student,values,link)
        state=preview(student,enrollment)
        if enrollment:
            entries=list(db.session.scalars(select(m['PaymentEntry']).where(m['PaymentEntry'].enrollment_id==enrollment.id)))
            if any(e.currency!=values['currency'] for e in entries):raise ValueError('Recorded payments use another currency. Escalate this record to the administrator.')
            if paid<sum((e.amount for e in entries),amount(0)):raise ValueError('Reported paid-to-date cannot be lower than recorded portal payments.')
        return enrollment,state
    def packet(identity,student=None):
        initial=db.get_or_404(Submission,identity)
        db.session.scalar(select(Task).where(Task.id==initial.task_id).with_for_update())
        row=db.session.scalar(select(Submission).where(Submission.id==identity).with_for_update().execution_options(populate_existing=True))
        if not row:abort(404)
        if student and row.student_id!=student.id:abort(403)
        if row.status!='submitted' or not active(row):raise ValueError('This submission is no longer awaiting review for the current assigned staff member. Request a fresh submission.')
        student=student or db.get_or_404(m['Student'],row.student_id)
        enrollment,state=validate(student,row.payload,db.get_or_404(Link,row.task_id))
        if (enrollment.id if enrollment else None)!=row.enrollment_id or state['version']!=row.financial_version:raise ValueError('The student enrollment or payment record changed after submission. Return this packet for an updated review.')
        return row,enrollment
    def finish(row,enrollment,data):
        row.status='approved';row.enrollment_id=enrollment.id;row.reviewed_at=now();row.updated_at=now();row.reviewer=app.extensions['member_profiles']['audit_actor']();row.approved_values=data
        task=db.session.get(Task,row.task_id);task.status='completed';task.updated_at=now();task.result='Enrollment submission approved by administrator.'
        link=db.session.get(Link,task.id)
        if not link.enrollment_id:link.enrollment_id=enrollment.id
        audit(row,'enrollment_submission_approved')
    @app.route('/staff/tasks/<int:task_id>/enrollment',methods=['GET','POST'])
    def staff_enrollment_prepare(task_id):
        member=getattr(g,'staff',None)
        if not member or not member.active or not member.verified_at:abort(403)
        task=db.session.scalar(select(Task).where(Task.id==task_id).with_for_update())
        if not task:abort(404)
        if task.member_id!=member.id:abort(403)
        link=db.get_or_404(Link,task_id);student=db.get_or_404(m['Student'],link.student_id)
        if db.session.get(m['StudentArchive'],student.id):abort(400,'Ask an administrator to restore this student record first.')
        row=latest(task_id)
        # A reassigned member starts a new version; earlier submitted packets remain immutable.
        owned=row if row and row.preparer_id==member.id else None
        values=dict(owned.payload) if owned else {k:'' for k in FIELDS}
        if not owned:
            values.update(student_kind=student.kind if student.kind in ('new','existing') else 'new',currency='USD',paid_total='0',frequency='monthly')
            if link.enrollment_id:
                e=db.get_or_404(Enrollment,link.enrollment_id);state=preview(student,e)
                values.update(course_id=str(e.course_id),cohort_id=str(e.cohort_id or ''),agreed_total=str(state['total']),currency=state['currency'],paid_total=str(state['paid']))
        if request.method=='POST':
            try:
                action=request.form.get('action')
                if action not in ('draft','submit'):abort(400,'Staff may save a draft or submit for administrator review only.')
                if any(k in request.form for k in ('decision','allow_access','records_confirmed','verify_reference')):abort(400,'Approval and payment verification require an administrator.')
                if request.form.get('version','')!=(version(row) if row else ''):raise ValueError('This task was updated in another tab. Reload before saving.')
                if owned and owned.status=='submitted':raise ValueError('This submission is awaiting administrator review and cannot be edited.')
                values={k:request.form.get(k,'').strip() for k in FIELDS}
                if any(len(v)>(5000 if k=='notes' else 100) for k,v in values.items()):raise ValueError('One of the fields is too long.')
                enrollment,state=validate(student,values,link) if action=='submit' else (None,None)
                if not owned or owned.status!='draft':
                    row=Submission(task_id=task_id,student_id=student.id,preparer_id=member.id,revision=(row.revision+1 if row else 1),status='draft');db.session.add(row)
                row.payload=values;row.updated_at=now()
                if action=='submit':
                    row.status='submitted';row.submitted_at=now();row.enrollment_id=enrollment.id if enrollment else None;row.financial_version=state['version'];task.status='in_progress';task.updated_at=now()
                db.session.flush();audit(row,'enrollment_submission_'+('submitted' if action=='submit' else 'draft'))
                if action=='submit':app.extensions['notifications']['enqueue'](student,'staff_enrollment_submitted',key='staff-enrollment:'+row.id,payload={'submission_id':row.id})
                db.session.commit();flash('Submitted for administrator review. Student access and verified payment records have not changed.' if action=='submit' else 'Enrollment draft saved.')
                return redirect(url_for('staff_enrollment_prepare',task_id=task_id))
            except (ValueError,IntegrityError) as error:
                db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'Another update was saved. Reload before continuing.');row=latest(task_id);owned=row if row and row.preparer_id==member.id else None
        courses=list(db.session.scalars(select(m['Course']).join(m['CoursePublication']).where(m['CoursePublication'].published.is_(True))))
        history=list(db.session.scalars(select(Submission).where(Submission.task_id==task_id).order_by(Submission.revision.desc())))
        return render_template('staff_enrollment_prepare.html',task=task,student=student,row=owned,values=values,version=version(row) if row else '',courses=courses,cohorts=list(db.session.scalars(select(m['Cohort']).order_by(m['Cohort'].start_date.desc()))),history=history)
    @app.get('/admin/enrollment-submissions')
    @h['admin_required']
    def admin_enrollment_submissions():
        rows=list(db.session.scalars(select(Submission).where(Submission.status!='draft').order_by(Submission.submitted_at.desc())))
        return render_template('admin_enrollment_submissions.html',rows=rows,active=active,student_for=lambda row:db.session.get(m['Student'],row.student_id))
    @app.route('/admin/enrollment-submissions/<identity>',methods=['GET','POST'])
    @h['admin_required']
    def admin_enrollment_submission(identity):
        row=db.get_or_404(Submission,identity)
        if row.status=='draft':abort(404)
        task=db.session.scalar(select(Task).where(Task.id==row.task_id).with_for_update());student=db.get_or_404(m['Student'],row.student_id)
        row=db.session.scalar(select(Submission).where(Submission.id==identity).with_for_update().execution_options(populate_existing=True))
        if request.method=='POST':
            app.extensions['integrations']['reauthenticate']()
            action=request.form.get('action');feedback=request.form.get('feedback','').strip()
            if action not in ('changes_requested','declined') or not 1<=len(feedback)<=2000:abort(400,'Choose a review outcome and record feedback.')
            if row.status!='submitted':abort(409,'This submission has already been reviewed.')
            row.status=action;row.feedback=feedback;row.reviewed_at=now();row.updated_at=now();row.reviewer=app.extensions['member_profiles']['audit_actor']();audit(row,'enrollment_submission_'+action)
            db.session.commit();flash('Submission '+action.replace('_',' ')+'. Existing student access and financial records were not changed.');return redirect(url_for('admin_enrollment_submission',identity=row.id))
        issue=''
        if row.status=='submitted':
            try:packet(row.id,student)
            except ValueError as error:issue=str(error)
        enrollment=db.session.get(Enrollment,row.enrollment_id) if row.enrollment_id else None
        return render_template('admin_enrollment_submission.html',row=row,task=task,student=student,preparer=db.session.get(Member,row.preparer_id),issue=issue,current=preview(student,enrollment),course=db.session.get(m['Course'],int(row.payload['course_id'])))
    def label(kind,value):
        try:identity=int(value)
        except (TypeError,ValueError):return 'Not selected'
        if not 0<identity<2147483648:return 'Not selected'
        item=db.session.get(m['Course'] if kind=='course_id' else m['Cohort'],identity)
        if not item:return 'Unavailable'
        return item.name if kind=='course_id' else f'{item.course.name} · {item.start_date} · {item.label}'
    @app.context_processor
    def context():
        return dict(enrollment_submission_label=label,staff_enrollment_latest=latest,staff_enrollment_pending=lambda task:bool((row:=latest(task.id)) and row.status=='submitted' and active(row)),enrollment_submission_count=lambda:sum(1 for row in db.session.scalars(select(Submission).where(Submission.status=='submitted')) if active(row)))
    app.extensions['staff_enrollment']=dict(packet=packet,finish=finish,pending=pending,requires_review=requires_review,latest=latest,active=active)
