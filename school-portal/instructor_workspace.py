"""Course-scoped class scheduling, attendance and assessment review for instructors."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from flask import abort,flash,g,redirect,render_template,request,url_for
from sqlalchemy import select


def install(app,db,m):
    Course,Class,Detail,Enrollment=(m[k] for k in ('Course','ClassSession','ScheduledClass','Enrollment'))
    def allowed(course_id):return bool(g.staff and g.staff.role=='instructor' and app.extensions['lesson_teaching']['can_teach'](course_id))
    def require(course_id):
        if not allowed(course_id):abort(403,'Open your Instructor profile and use an assigned course.')
    def audit(action,detail):db.session.add(m['StaffAudit'](actor='staff:'+str(g.staff.id),member_id=g.staff.id,action=action,detail=detail[:1000]))
    @app.route('/teaching/workspace',methods=['GET','POST'])
    def instructor_workspace():
        if not g.staff or g.staff.role!='instructor':abort(403)
        courses=[c for c in db.session.scalars(select(Course).order_by(Course.name)) if allowed(c.id)]
        if request.method=='POST':
            cid=request.form.get('course_id',type=int);require(cid);course=db.get_or_404(Course,cid)
            try:
                action=request.form.get('action');title=request.form.get('title','').strip()
                if not 1<=len(title)<=150:raise ValueError('Enter a title of up to 150 characters.')
                if action=='class':
                    zone=request.form.get('timezone','America/Toronto')
                    if zone not in m['TIMEZONES']:raise ValueError('Choose a valid time zone.')
                    local=datetime.fromisoformat(request.form.get('starts_at',''))
                    if local.tzinfo:raise ValueError('Enter local time and choose its time zone.')
                    zoned=local.replace(tzinfo=ZoneInfo(zone))
                    if zoned.astimezone(timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None)!=local or zoned.utcoffset()!=local.replace(tzinfo=ZoneInfo(zone),fold=1).utcoffset():raise ValueError('Choose an unambiguous local time.')
                    from lesson_teaching import media_url
                    kind,link=media_url(request.form.get('join_url',''))
                    if kind!='zoom' or '/j/' not in link:raise ValueError('Use a Zoom meeting link for live classes.')
                    duration=int(request.form.get('duration','120'))
                    if not 15<=duration<=600:raise ValueError('Choose a duration between 15 and 600 minutes.')
                    cohort_id=request.form.get('cohort_id',type=int)
                    if cohort_id and db.get_or_404(m['Cohort'],cohort_id).course_id!=cid:abort(403)
                    lesson_id=request.form.get('lesson_id',type=int)
                    if lesson_id and db.get_or_404(m['Lesson'],lesson_id).course_id!=cid:abort(403)
                    class_id=request.form.get('class_id',type=int)
                    row=db.get_or_404(Class,class_id) if class_id else Class(course_id=cid)
                    if row.course_id!=cid:abort(403)
                    row.title=title;row.starts_at=zoned.astimezone(timezone.utc);row.registration_url=''
                    if row.joining:row.joining.url=link
                    else:row.joining=m['ClassJoinLink'](url=link)
                    db.session.add(row);db.session.flush();detail=db.session.get(Detail,row.id) or Detail(class_id=row.id)
                    detail.cohort_id=cohort_id;detail.duration=duration;detail.timezone=zone;detail.instructor=g.staff.name;detail.published=request.form.get('published')=='yes';detail.join_minutes=15;detail.revision=(detail.revision or 0)+1;db.session.add(detail);db.session.flush()
                    if lesson_id:
                        association=db.session.get(m['LessonClass'],row.id) or m['LessonClass'](class_id=row.id,lesson_id=lesson_id)
                        association.lesson_id=lesson_id;db.session.add(association)
                    app.extensions['global_experience']['queue_class'](row,detail);audit('instructor_class_saved',str(row.id));db.session.commit()
                    if detail.published:app.extensions['integrations']['dispatch'](class_id=row.id)
                    flash('Class saved. Student publication, cohort and notification rules apply.')
                elif action=='assessment':
                    kind=request.form.get('kind');instructions=request.form.get('instructions','').strip()
                    if kind not in ('assignment','quiz') or not 1<=len(instructions)<=10000:raise ValueError('Choose an assignment or quiz and provide instructions.')
                    row=m['Assessment'](course_id=cid,title=title,kind=kind,instructions=instructions);db.session.add(row);db.session.flush()
                    lesson_id=request.form.get('lesson_id',type=int)
                    if lesson_id:
                        if db.get_or_404(m['Lesson'],lesson_id).course_id!=cid:abort(403)
                        db.session.add(m['LessonActivity'](lesson_id=lesson_id,assessment_id=row.id))
                    audit('instructor_assessment_created',str(row.id));db.session.commit();flash('Assessment created.')
                else:abort(400)
                return redirect(url_for('instructor_workspace',course_id=cid))
            except ValueError as error:db.session.rollback();flash(str(error))
        selected=request.args.get('course_id',type=int) or (courses[0].id if courses else None)
        if selected:require(selected)
        return render_template('instructor_workspace.html',courses=courses,selected=selected,classes=list(db.session.scalars(select(Class).where(Class.course_id==selected).order_by(Class.starts_at.desc()).limit(50))) if selected else [],lessons=list(db.session.scalars(select(m['Lesson']).where(m['Lesson'].course_id==selected).order_by(m['Lesson'].position))) if selected else [],cohorts=list(db.session.scalars(select(m['Cohort']).where(m['Cohort'].course_id==selected))) if selected else [],assessments=list(db.session.scalars(select(m['Assessment']).where(m['Assessment'].course_id==selected))) if selected else [])
    @app.route('/teaching/classes/<int:class_id>/attendance',methods=['GET','POST'])
    def instructor_attendance(class_id):
        row=db.get_or_404(Class,class_id);require(row.course_id);detail=db.session.get(Detail,row.id)
        query=select(Enrollment).where(Enrollment.course_id==row.course_id,Enrollment.status=='approved',Enrollment.payment_status=='verified')
        if detail and detail.cohort_id:query=query.where(Enrollment.cohort_id==detail.cohort_id)
        enrollments=[e for e in db.session.scalars(query) if not db.session.get(m['StudentArchive'],e.student_id) and not (db.session.get(m['StudentControl'],e.student_id) and not db.session.get(m['StudentControl'],e.student_id).active)]
        if request.method=='POST':
            if m['aware'](row.starts_at)>m['now']()+timedelta(minutes=15):abort(400,'Attendance opens 15 minutes before the class.')
            selected=set(request.form.getlist('student_ids'))
            if not selected.issubset({e.student_id for e in enrollments}):abort(403)
            for sid in selected:
                if not db.session.scalar(select(m['Attendance'].id).where(m['Attendance'].student_id==sid,m['Attendance'].class_id==row.id)):db.session.add(m['Attendance'](student_id=sid,class_id=row.id))
            audit('instructor_attendance_recorded',f'Class {row.id}; students '+','.join(sorted(selected)));db.session.commit();return redirect(request.path)
        present=set(db.session.scalars(select(m['Attendance'].student_id).where(m['Attendance'].class_id==row.id)))
        return render_template('instructor_attendance.html',classroom=row,enrollments=enrollments,present=present)
    @app.route('/teaching/assessments/<int:assessment_id>/reviews',methods=['GET','POST'])
    def instructor_assessment_reviews(assessment_id):
        assessment=db.get_or_404(m['Assessment'],assessment_id);require(assessment.course_id)
        if request.method=='POST':
            row=db.get_or_404(m['Submission'],request.form.get('submission_id',type=int))
            if row.assessment_id!=assessment.id:abort(403)
            score=request.form.get('score',type=int);feedback=request.form.get('feedback','').strip()
            if score is None or not 0<=score<=100 or not 1<=len(feedback)<=3000:abort(400,'Provide a score from 0 to 100 and feedback.')
            row.score=score;row.feedback=feedback;row.reviewed_at=m['now']();audit('instructor_submission_reviewed',str(row.id));db.session.add(m['Notification'](student_id=row.student_id,title='Your assignment result is ready',body=assessment.title+': '+str(score)+'/100'));db.session.commit();return redirect(request.path)
        return render_template('instructor_assessment_reviews.html',assessment=assessment,submissions=list(db.session.scalars(select(m['Submission']).where(m['Submission'].assessment_id==assessment.id))))
