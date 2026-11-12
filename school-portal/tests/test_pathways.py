from datetime import date, timedelta
from decimal import Decimal
import io
from sqlalchemy import select
from app import db,Course,Enrollment,EnrollmentPaymentSchedule,CybersecurityUnit,LabExercise,ClassDelivery,StaffMember,LabEvidence,Submission,now
from pathways import cyber_starts,seed_pathways
from test_portal import admin,register,csrf,post
from test_lessons_files import MemoryStorage


def setup(app):
    manager,student=app.test_client(),app.test_client()
    admin(manager);register(student,country='Canada')
    app.config['FILE_STORAGE']=MemoryStorage()
    with app.app_context():
        ids={c.slug:c.id for c in db.session.scalars(select(Course))}
        labs={l.course_id:l.id for l in db.session.scalars(select(LabExercise).where(LabExercise.number==1))}
    return manager,student,ids,labs


def test_public_levels_prices_curriculum_and_intakes(app):
    manager,student,ids,labs=setup(app)
    public=app.test_client()
    catalog=public.get('/programs');assert catalog.status_code==200
    assert catalog.data.count(b'<article')==4
    for prog in ('cybersecurity','data-analytics'):
        assert public.get('/programs/'+prog).status_code==200
        for level in ('foundation','intermediate','advanced'):
            r=public.get('/programs/'+prog+'/'+level);assert r.status_code==200
            assert b'Everything you will learn' in r.data
            assert public.get('/programs/'+prog+'/'+level+'?tab=resources').status_code==403
    r=student.get('/programs/cybersecurity/foundation')
    assert b'278.60' in r.data and b'139.30' in r.data and b'USD' not in r.data
    assert r.data.count(b'data-lesson-row')==8
    assert b'January 25, 2027' in student.get('/programs/cybersecurity/foundation/enroll').data
    assert cyber_starts(date(2027,3,1))[0]==date(2027,3,29)
    with app.app_context():
        assert db.session.query(CybersecurityUnit).count()==24
        assert db.session.query(LabExercise).count()==30
        import app as m
        with db.engine.begin() as conn:seed_pathways(conn,vars(m));seed_pathways(conn,vars(m))
        assert db.session.query(LabExercise).count()==30


def test_seventh_payment_dates_and_full_option_snapshot(app):
    from app import Cohort
    manager,student,ids,labs=setup(app)
    page=student.get('/programs/cybersecurity/foundation/enroll')
    with app.app_context():cid=db.session.scalar(select(Cohort.id).where(Cohort.course_id==ids['cybersecurity'],Cohort.start_date==date(2027,1,25)))
    r=student.post('/programs/cybersecurity/foundation/enroll',data={'csrf':csrf(student,'/portal'),'cohort_id':cid,'plan':'installments'},follow_redirects=True)
    assert r.status_code==200
    with app.app_context():
        e=db.session.scalar(select(Enrollment).where(Enrollment.course_id==ids['cybersecurity']))
        plan=db.session.get(EnrollmentPaymentSchedule,e.id)
        assert plan.total==Decimal('199')
        assert [p['due'] for p in plan.stages]==['2027-01-07','2027-02-07']
        assert [p['amount'] for p in plan.stages]==['99.50','99.50']
    student.get('/programs/cybersecurity/advanced/enroll')
    with app.app_context():cid=db.session.scalar(select(Cohort.id).where(Cohort.course_id==ids['cybersecurity-advanced']))
    r=student.post('/programs/cybersecurity/advanced/enroll',data={'csrf':csrf(student,'/portal'),'cohort_id':cid,'plan':'full'},follow_redirects=True)
    assert r.status_code==200
    with app.app_context():
        e=db.session.scalar(select(Enrollment).where(Enrollment.course_id==ids['cybersecurity-advanced']))
        plan=db.session.get(EnrollmentPaymentSchedule,e.id)
        assert len(plan.stages)==1 and plan.stages[0]['amount']=='401.00'
    assert student.get('/programs/cybersecurity/advanced?tab=labs').status_code==403


def publish(client,cid,lid,cohort='',state='published'):
    start=(now()+timedelta(days=5)).strftime('%Y-%m-%dT18:00')
    due=(now()+timedelta(days=7)).strftime('%Y-%m-%dT23:00')
    return client.post('/teaching/labs/save',data={'csrf':csrf(client,'/teaching/labs'),'course_id':cid,'lab_id':lid,'title':'Lab network exercise','instructions':'Use the isolated training environment.','cohort_id':cohort,'starts_at':start,'timezone':'America/Toronto','duration':'180','due_at':due,'join_url':'https://zoom.us/j/lab-test','state':state},follow_redirects=True)


def test_scheduled_labs_scoped_submissions_files_and_grades(app):
    from app import Student,Cohort
    manager,student,ids,labs=setup(app)
    cid=ids['cybersecurity'];lid=labs[cid]
    assert student.get('/teaching/labs').status_code==403
    assert manager.get('/teaching/labs').status_code==200
    # Draft labs do not appear through either classroom or legacy assessment routes.
    with app.app_context():
        sid=db.session.scalar(select(Student.id));cohort=db.session.scalar(select(Cohort).where(Cohort.course_id==cid))
        e=Enrollment(student_id=sid,course_id=cid,cohort_id=cohort.id,status='approved',payment_status='verified');db.session.add(e);db.session.commit();cohortid=cohort.id
        aid=db.session.get(LabExercise,lid).assessment_id
    assert student.get('/labs/'+str(lid)).status_code==403
    assert student.post('/assessment/'+str(aid)+'/submit',data={'csrf':csrf(student,'/portal'),'answer':'Bypass'}).status_code==403
    assert publish(manager,cid,lid,cohortid).status_code==200
    assert student.get('/labs/'+str(lid)).status_code==200
    assert b'Lab network exercise' in student.get('/programs/cybersecurity/foundation?tab=labs').data
    with app.app_context():
        l=db.session.get(LabExercise,lid);assert l.published and l.assessment.due_at
        assert db.session.query(ClassDelivery).filter_by(class_id=l.class_id).count()==1
    outsider=app.test_client();register(outsider,'outsider@example.com')
    assert outsider.get('/labs/'+str(lid)).status_code==403
    r=student.post('/labs/'+str(lid)+'/submit',data={'csrf':csrf(student,'/portal'),'answer':'Commands, screenshots and conclusions.','url':'','evidence':(io.BytesIO(b'%PDF-1.7\nLab report'),'report.pdf')},follow_redirects=True)
    assert r.status_code==200 and b'Awaiting instructor review' in r.data
    with app.app_context():
        ev=db.session.scalar(select(LabEvidence));evid=ev.id;subid=ev.submission_id
    assert student.get('/labs/evidence/'+evid).status_code==200
    assert outsider.get('/labs/evidence/'+evid).status_code==403
    r=manager.post('/teaching/labs/'+str(lid)+'/reviews',data={'csrf':csrf(manager,'/admin'),'submission_id':subid,'score':'88','feedback':'Good evidence.'},follow_redirects=True)
    assert r.status_code==200
    assert b'88/100' in student.get('/labs/'+str(lid)).data
    publish(manager,cid,lid,cohortid,state='draft')
    assert student.get('/labs/'+str(lid)).status_code==403


def test_instructors_can_schedule_but_other_staff_cannot(app):
    manager,student,ids,labs=setup(app)
    from test_staff_portal import setup as staff_setup,create,login
    _,sent=staff_setup(app)
    iid=create(app,name='Lab Instructor',role='instructor',department='Teaching')
    sid=create(app,name='Lab Staff',role='staff',department='Teaching')
    instructor,staff=login(app,sent,iid),login(app,sent,sid)
    assert staff.get('/teaching/labs').status_code==403
    assert instructor.get('/teaching/labs').status_code==200
    assert publish(instructor,ids['cybersecurity'],labs[ids['cybersecurity']],state='draft').status_code==200
