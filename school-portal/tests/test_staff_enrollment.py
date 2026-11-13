"""Staff preparation cannot replace administrator financial verification/approval."""
import re
from datetime import date,timedelta
import pytest
from sqlalchemy import select
from app import db,Student,Enrollment,EnrollmentPaymentSchedule,PaymentEntry,StaffTask,TaskRecord,StaffEnrollmentSubmission,StaffMember,EnrollmentOpeningCredit,LifecycleEvent,EnrollmentReview
from test_admin_enrollment import setup as learner_setup,review
from test_staff_portal import setup,create,login,post
from test_portal import csrf


def scenario(app,eid=None):
    student,manager,sid,cid,cohort=learner_setup(app);manager,sent=setup(app)
    staff_id=create(app,'Enrollment Officer');worker=login(app,sent,staff_id)
    with app.app_context():
        task=StaffTask(member_id=staff_id,title='Collect tuition agreement',due_on=date.today()+timedelta(days=2));db.session.add(task);db.session.flush();tid=task.id
        db.session.add(TaskRecord(task_id=tid,student_id=sid,kind='student'));db.session.commit()
    return student,manager,sid,cid,cohort,worker,tid,staff_id,sent


def submit(worker,tid,cid,cohort,**extra):
    path=f'/staff/tasks/{tid}/enrollment';page=worker.get(path);assert page.status_code==200
    version=re.search(rb'name="version" value="([^"]*)"',page.data)
    if not version:return page
    fields=dict(csrf=csrf(worker,path),version=version[1].decode(),action='submit',course_id=cid,cohort_id=cohort,student_kind='existing',agreed_total='1000',paid_total='400',currency='USD',installment_amount='200',next_due=(date.today()+timedelta(days=20)).isoformat(),frequency='monthly',notes='Contacted student, confirmed agreement. Historical bank references to be verified by admin.')
    fields.update(extra)
    return worker.post(path,data=fields,follow_redirects=True)


def packet_id(app):
    with app.app_context():return db.session.scalar(select(StaffEnrollmentSubmission.id).order_by(StaffEnrollmentSubmission.revision.desc()))


def approve_packet(manager,sid,cid,cohort,identity,**extra):
    path=f'/admin/students/{sid}/enrollment-review?submission_id={identity}'
    page=manager.get(path);assert page.status_code==200,page.location
    version=re.search(rb'name="version" value="([^"]+)"',page.data)[1].decode()
    fields=dict(csrf=csrf(manager,path),submission_id=identity,version=version,decision='approve',course_id=cid,cohort_id=cohort,student_kind='existing',mode='custom',agreed_total='1000',paid_total='400',currency='USD',installment_amount='200',frequency='monthly',next_due=(date.today()+timedelta(days=20)).isoformat(),confirm_program='yes',confirm_tuition='yes',records_confirmed='yes',allow_access='yes',replace_terms='yes',reason='Independently verified funds and student agreement.',admin_password='test-admin-password')
    fields.update(extra)
    return manager.post(path,data=fields,follow_redirects=True)


def test_staff_draft_submit_admin_approval_is_only_financial_mutation(app):
    student,manager,sid,cid,cohort,worker,tid,staff_id,sent=scenario(app)
    assert b'Prepare enrollment for review' in worker.get(f'/staff/tasks/{tid}/record').data
    assert b'Save draft' in submit(worker,tid,cid,cohort,action='draft').data
    with app.app_context():
        assert not db.session.scalar(select(Enrollment.id))
        assert db.session.scalar(select(StaffEnrollmentSubmission)).status=='draft'
    response=submit(worker,tid,cid,cohort)
    assert response.status_code==200 and b'Awaiting administrator review' in response.data
    identity=packet_id(app)
    with app.app_context():
        assert not db.session.scalar(select(Enrollment.id))
        assert not db.session.scalar(select(EnrollmentOpeningCredit.enrollment_id))
        assert db.session.get(StaffTask,tid).status=='in_progress'
        event=db.session.scalar(select(LifecycleEvent).where(LifecycleEvent.kind=='staff_enrollment_submitted'));assert event and event.payload['submission_id']==identity
        assert app.extensions['staff_enrollment']['pending'](db.session.get(Student,sid))
        assert 'Staff enrollment submission' in app.extensions['account_retention']['evidence'](db.session.get(Student,sid))[1]
    assert worker.get('/admin/enrollment-submissions').status_code==302
    assert worker.get('/admin/enrollment-submissions/'+identity).status_code==302
    assert b'Enrollment Officer' in manager.get('/admin/enrollment-submissions/'+identity).data
    approval=manager.get(f'/admin/students/{sid}/enrollment-review?submission_id={identity}');assert b'name="submission_id"' in approval.data and b'value="400"' in approval.data
    assert b'name="confirm_tuition" value="yes" required' in approval.data
    assert post(worker,f'/staff/tasks/{tid}',status='completed',result='Submitted').status_code==409
    response=approve_packet(manager,sid,cid,cohort,identity);assert response.status_code==200,response.data[-3000:]
    with app.app_context():
        row=db.session.get(StaffEnrollmentSubmission,identity);e=db.session.get(Enrollment,row.enrollment_id)
        assert row.status=='approved' and row.approved_values['balance']=='600.00'
        assert e.status=='approved' and e.payment_status=='verified'
        assert db.session.get(EnrollmentOpeningCredit,e.id).amount==400
        assert db.session.get(EnrollmentPaymentSchedule,e.id).total==1000
        assert not db.session.scalar(select(PaymentEntry.id))
        assert db.session.get(StaffTask,tid).status=='completed'
        assert db.session.get(TaskRecord,tid).enrollment_id==e.id
        assert row.payload['paid_total']=='400'
    assert b'Final administrator-approved terms' in worker.get(f'/staff/tasks/{tid}/enrollment').data


@pytest.mark.parametrize('field,value',[('allow_access','yes'),('decision','approve'),('records_confirmed','yes'),('action','approve')])
def test_staff_cannot_forge_approval(app,field,value):
    _,_,sid,cid,cohort,worker,tid,_,_=scenario(app)
    assert submit(worker,tid,cid,cohort,**{field:value}).status_code==400
    with app.app_context():assert not db.session.scalar(select(Enrollment.id))


def test_scope_reassignment_and_immutable_submission(app):
    _,manager,sid,cid,cohort,worker,tid,staff_id,sent=scenario(app)
    outsider_id=create(app,'Other Officer');outsider=login(app,sent,outsider_id)
    assert outsider.get(f'/staff/tasks/{tid}/enrollment').status_code==403
    submit(worker,tid,cid,cohort);identity=packet_id(app)
    fields=dict(csrf=csrf(worker,'/staff'),action='draft',version='stale',paid_total='1000')
    assert b'awaiting administrator' in worker.post(f'/staff/tasks/{tid}/enrollment',data=fields,follow_redirects=True).data or b'updated in another tab' in worker.post(f'/staff/tasks/{tid}/enrollment',data=fields,follow_redirects=True).data
    post(manager,f'/admin/team/tasks/{tid}/reassign',page='/admin/team',member_id=outsider_id)
    assert worker.get(f'/staff/tasks/{tid}/enrollment').status_code==403
    assert b'no longer awaiting review' in manager.get(f'/admin/students/{sid}/enrollment-review?submission_id={identity}',follow_redirects=True).data
    with app.app_context():
        assert not app.extensions['staff_enrollment']['pending'](db.session.get(Student,sid))
        assert db.session.get(StaffEnrollmentSubmission,identity).payload['paid_total']=='400'
    assert b'Save draft' in outsider.get(f'/staff/tasks/{tid}/enrollment').data
    submit(outsider,tid,cid,cohort)
    with app.app_context():assert db.session.scalar(select(StaffEnrollmentSubmission).order_by(StaffEnrollmentSubmission.revision.desc())).revision==2


@pytest.mark.parametrize('outcome',['changes_requested','declined'])
def test_return_decline_keep_history_and_allow_new_version(app,outcome):
    _,manager,sid,cid,cohort,worker,tid,_,_=scenario(app);submit(worker,tid,cid,cohort);identity=packet_id(app)
    result=post(manager,'/admin/enrollment-submissions/'+identity,page='/admin/team',action=outcome,feedback='Confirm missing payment reference.',admin_password='test-admin-password');assert result.status_code==200
    assert b'Confirm missing payment reference.' in worker.get(f'/staff/tasks/{tid}/enrollment').data
    submit(worker,tid,cid,cohort,paid_total='500');new_id=packet_id(app);assert new_id!=identity
    with app.app_context():
        old=db.session.get(StaffEnrollmentSubmission,identity);new=db.session.get(StaffEnrollmentSubmission,new_id)
        assert old.status==outcome and old.payload['paid_total']=='400'
        assert new.revision==2 and new.payload['paid_total']=='500'
        assert not db.session.scalar(select(Enrollment.id))


@pytest.mark.parametrize('field,value',[('confirm_program',''),('confirm_tuition',''),('records_confirmed',''),('allow_access',''),('admin_password','wrong'),('decision','save'),('decision','decline')])
def test_admin_verification_is_mandatory(app,field,value):
    _,manager,sid,cid,cohort,worker,tid,_,_=scenario(app);submit(worker,tid,cid,cohort);identity=packet_id(app)
    result=approve_packet(manager,sid,cid,cohort,identity,**{field:value});assert result.status_code in (200,403)
    with app.app_context():
        assert not db.session.scalar(select(Enrollment.id))
        assert db.session.get(StaffEnrollmentSubmission,identity).status=='submitted'


def test_stale_payment_snapshot_blocks_review_and_new_payment_does_not_grant_access(app):
    _,manager,sid,cid,cohort,worker,tid,_,_=scenario(app)
    with app.app_context():
        e=Enrollment(student_id=sid,course_id=cid,cohort_id=cohort);db.session.add(e);db.session.commit();eid=e.id
    submit(worker,tid,cid,cohort);identity=packet_id(app)
    with app.app_context():
        e=db.session.get(Enrollment,eid);e.payment_status='verified';e.status='approved';db.session.commit()
        assert e.status=='pending'
    page=manager.get(f'/admin/students/{sid}/enrollment-review?submission_id={identity}',follow_redirects=True)
    assert b'changed after submission' in page.data
    with app.app_context():assert db.session.get(StaffEnrollmentSubmission,identity).status=='submitted'


def test_payment_assignment_program_scope_and_existing_access_preserved(app):
    _,manager,sid,cid,cohort,worker,tid,_,_=scenario(app);review(manager,sid,cid,cohort)
    with app.app_context():
        eid=db.session.scalar(select(Enrollment.id));link=db.session.get(TaskRecord,tid);link.enrollment_id=eid;link.kind='payment';db.session.commit()
        from app import Course,Cohort
        other=db.session.scalar(select(Cohort).where(Cohort.course_id!=cid));other_course,other_cohort=other.course_id,other.id
    response=submit(worker,tid,other_course,other_cohort);assert b'restricted to its existing program' in response.data
    submit(worker,tid,cid,cohort);identity=packet_id(app)
    with app.app_context():
        e=db.session.get(Enrollment,eid);assert e.status=='approved' and e.payment_status=='verified'
        assert app.extensions['admin_enrollment']['approved'](e)
    post(manager,'/admin/enrollment-submissions/'+identity,page='/admin/team',action='declined',feedback='Already enrolled; no revision needed.',admin_password='test-admin-password')
    with app.app_context():assert db.session.get(Enrollment,eid).status=='approved' and db.session.get(EnrollmentReview,eid).status=='approved'


def test_draft_stale_tab_and_deactivated_staff_protection(app):
    _,_,sid,cid,cohort,worker,tid,staff_id,_=scenario(app)
    path=f'/staff/tasks/{tid}/enrollment';page=worker.get(path);token=csrf(worker,path)
    submit(worker,tid,cid,cohort,action='draft')
    result=worker.post(path,data={'csrf':token,'version':'','action':'draft','notes':'Lost edit'},follow_redirects=True)
    assert b'updated in another tab' in result.data
    with app.app_context():assert not app.extensions['staff_enrollment']['pending'](db.session.get(Student,sid))
    submit(worker,tid,cid,cohort)
    with app.app_context():
        db.session.get(StaffMember,staff_id).active=False;db.session.commit()
        assert not app.extensions['staff_enrollment']['pending'](db.session.get(Student,sid))
    assert worker.get(path).status_code in (302,403)


def test_returned_packet_does_not_allow_provider_to_bypass_admin(app):
    _,manager,sid,cid,cohort,worker,tid,_,_=scenario(app)
    with app.app_context():
        e=Enrollment(student_id=sid,course_id=cid,cohort_id=cohort);db.session.add(e);db.session.commit();eid=e.id
    submit(worker,tid,cid,cohort);identity=packet_id(app)
    post(manager,'/admin/enrollment-submissions/'+identity,page='/admin/team',action='changes_requested',feedback='Clarify payment evidence.',admin_password='test-admin-password')
    with app.app_context():
        e=db.session.get(Enrollment,eid);e.payment_status='verified';e.status='approved';db.session.commit()
        assert e.status=='pending'
        assert not app.extensions['staff_enrollment']['pending'](db.session.get(Student,sid))
