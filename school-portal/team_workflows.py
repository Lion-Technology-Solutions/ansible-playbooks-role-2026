"""Record-linked tasks and scoped, meeting-friendly team operations."""
from lesson_tasks import task_number
from datetime import date
from flask import abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select


def define(db, now):
    class TaskRecord(db.Model):
        __tablename__ = 'staff_task_records'
        task_id = db.Column(db.Integer, db.ForeignKey('staff_tasks.id'), primary_key=True)
        enrollment_id = db.Column(db.Integer, db.ForeignKey('portal_enrollments.id'))
        student_id = db.Column(db.String(36), db.ForeignKey('portal_students.id'), nullable=False)
        kind = db.Column(db.String(20), nullable=False)
        task=db.relationship('StaffTask')
        student=db.relationship('Student')
    return dict(TaskRecord=TaskRecord)


def install(app, db, m, guards):
    Task, Link, Member, Enrollment = (m[k] for k in ('StaffTask','TaskRecord','StaffMember','Enrollment'))
    Report, Audit = m['StaffReport'], m['StaffAudit']
    def actor():
        if session.get('admin'): return 'admin:1'
        if not getattr(g,'staff',None): abort(403)
        return 'staff:'+str(g.staff.id)
    def allowed(member):
        return app.extensions['staff_portal']['can'](member,'tasks')
    def people():
        actor()
        return [s for s in db.session.scalars(select(Member).order_by(Member.name)) if allowed(s)]
    def task_allowed(task):
        member=db.get_or_404(Member,task.member_id)
        if not member.active or not allowed(member): abort(403)
        return member
    def record_info(link):
        enrollment=db.session.get(Enrollment,link.enrollment_id) if link.enrollment_id else None
        return dict(enrollment=enrollment,student=db.get_or_404(m['Student'],link.student_id),kind=link.kind)
    @app.route('/staff/workboard',methods=['GET','POST'])
    def staff_workboard():
        members=people(); manager=bool(session.get('admin') or (g.staff and g.staff.role=='supervisor'))
        if request.method=='POST':
            if app.extensions['student_advisors']['enabled']():abort(409,'Student and payment follow-up is assigned automatically through Student Advisors.')
            # Record allocation is an administrator capability; supervision alone
            # does not grant access to the student/payment directory.
            if not session.get('admin'): abort(403)
            target=db.get_or_404(Member,request.form.get('member_id',type=int))
            if not target.active or not target.verified_at: abort(400,'Choose an active, verified colleague.')
            kind=request.form.get('kind'); student_ids=request.form.getlist('student_ids'); ids=request.form.getlist('enrollment_ids')
            if kind=='payment' and student_ids:abort(400,'Select payment records for payment follow-up.')
            if kind not in ('student','payment') or not 1<=len(set(ids))+len(set(student_ids))<=100: abort(400,'Select up to 100 records.')
            title=request.form.get('title','').strip(); instructions=request.form.get('instructions','').strip()
            priority=request.form.get('priority','normal')
            if not 1<=len(title)<=160 or len(instructions)>5000 or priority not in ('normal','high','urgent'): abort(400)
            try: due=date.fromisoformat(request.form.get('due_on',''))
            except ValueError: abort(400,'Choose a due date.')
            records=[]
            for identity in set(student_ids):records.append((None,db.get_or_404(m['Student'],identity)))
            for identity in set(ids):
                try: records.append((db.get_or_404(Enrollment,int(identity)),None))
                except ValueError: abort(400)
            deliveries=[]
            for enrollment,student in records:
                student=student or enrollment.student
                task=Task(member_id=target.id,title=title,description=instructions,due_on=due,priority=priority)
                db.session.add(task); db.session.flush();app.extensions['task_alerts']['assigned'](task)
                deliveries.append(app.extensions['staff_portal']['mail'](target,'task','task:'+str(task.id),target.email,'New LionTech staff task',f'{task_number(task)} · {title}\nDue: {due}\nView your assigned task in the portal: https://www.liontechacademy.com/staff/workboard').id)
                db.session.add(Link(task_id=task.id,enrollment_id=enrollment.id if enrollment else None,student_id=student.id,kind=kind))
                db.session.add(Audit(actor=actor(),member_id=target.id,action='record_task_assigned',detail=f'Task {task.id}; {kind}; student {student.id}'))
            db.session.commit(); app.extensions['staff_portal']['dispatch'](deliveries); flash(f'{len(records)} record tasks assigned.');return redirect(url_for('staff_workboard'))
        query=select(Task).where(Task.member_id.in_([s.id for s in members])).order_by(Task.due_on,Task.id)
        tasks=list(db.session.scalars(query)); selected=request.args.get('member_id',type=int)
        if selected: tasks=[t for t in tasks if t.member_id==selected]
        status=request.args.get('status','')
        if status in ('assigned','in_progress','completed'): tasks=[t for t in tasks if t.status==status]
        links={t.task_id:t for t in db.session.scalars(select(Link).where(Link.task_id.in_([t.id for t in tasks])))}
        records=list(db.session.scalars(select(Enrollment).order_by(Enrollment.created_at.desc()))) if session.get('admin') else []
        return render_template('staff_workboard.html',tasks=tasks,people=members,records=records,students=list(db.session.scalars(select(m['Student']).order_by(m['Student'].name))) if session.get('admin') else [],selected_students=set(request.args.getlist('student_id')[:100]),links=links,record_info=record_info,today=m['now']().date(),manager=manager)
    @app.get('/staff/tasks/<int:task_id>/record')
    def staff_task_record(task_id):
        actor(); task=db.get_or_404(Task,task_id);task_allowed(task)
        link=db.get_or_404(Link,task_id); info=record_info(link)
        db.session.add(Audit(actor=actor(),member_id=task.member_id,action='assigned_record_opened',detail=f'Task {task.id}'));db.session.commit()
        payments=list(db.session.scalars(select(m['PaymentEntry']).where(m['PaymentEntry'].enrollment_id==info['enrollment'].id))) if link.kind=='payment' else []
        return render_template('staff_task_record.html',task=task,payments=payments,**info)
    @app.post('/admin/team/tasks/<int:task_id>/reassign')
    @guards['admin_required']
    def staff_task_reassign(task_id):
        task=db.session.scalar(select(Task).where(Task.id==task_id).with_for_update())
        if not task:abort(404)
        target=db.get_or_404(Member,request.form.get('member_id',type=int))
        if not target.active or not target.verified_at:abort(400)
        if db.session.scalar(select(m['StudentAdvisor']).where(m['StudentAdvisor'].task_id==task.id)) or db.session.scalar(select(m['AdvisorFollowup']).where(m['AdvisorFollowup'].task_id==task.id)):abort(409,'Student advisors are allocated automatically.')
        previous=task.member_id;task.member_id=target.id;task.updated_at=m['now']();app.extensions['task_alerts']['assigned'](task)
        db.session.add(Audit(actor=actor(),member_id=target.id,action='task_reassigned',detail=f'Task {task.id}; previous staff {previous}'));db.session.commit()
        return redirect(url_for('staff_workboard'))
    @app.get('/staff/people/<int:member_id>/reports')
    def staff_member_reports(member_id):
        actor();member=db.get_or_404(Member,member_id)
        if not app.extensions['staff_portal']['can'](member,'reports'):abort(403)
        reports=list(db.session.scalars(select(Report).where(Report.member_id==member.id,Report.latest>0).order_by(Report.report_date.desc())))
        return render_template('staff_member_reports.html',member=member,reports=reports)
    @app.context_processor
    def context():return dict(task_record_link=lambda identity:db.session.get(Link,identity))
