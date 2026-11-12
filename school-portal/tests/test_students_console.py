import csv,io,re
from sqlalchemy import select
from app import db,Student,StudentIdentity,Enrollment,StudentArchive,StudentControl,TuitionControl,now
from test_portal import register,admin,analytics,request_enrollment


def fixture(app):
    manager,student=app.test_client(),app.test_client();admin(manager);register(student)
    cid,cohort=analytics(app);request_enrollment(student,cid,cohort)
    with app.app_context():
        s=db.session.scalar(select(Student));sid=s.id;number=s.student_number
        e=db.session.scalar(select(Enrollment));eid=e.id
    return manager,sid,number,eid,cid,cohort


def test_directory_search_filters_and_no_enrollment_accounts(app):
    manager,sid,number,eid,cid,cohort=fixture(app)
    second=app.test_client();register(second,email='unselected@example.test')
    with app.app_context():
        other=db.session.scalar(select(Student).where(Student.id!=sid));other.name='No Enrollment Student';db.session.commit()
    result=manager.get('/admin?view=students')
    assert result.status_code==200
    assert b'No Enrollment Student' in result.data and b'No program selected' in result.data
    assert result.data.count(b'data-select-student')==2
    assert number.encode() in manager.get('/admin?view=students&q='+number).data
    assert b'No Enrollment Student' not in manager.get('/admin?view=students&program='+str(cid)).data
    assert manager.get('/admin?view=students&payment=not-real').status_code==400
    assert manager.get('/admin?view=students&year=nope').status_code==400
    assert b'No students found' in manager.get('/admin?view=students&q=no-match').data


def test_directory_deduplicates_and_preserves_account_and_access_states(app):
    manager,sid,number,eid,cid,cohort=fixture(app)
    with app.app_context():
        e=db.session.get(Enrollment,eid);e.payment_status='verified';e.status='approved';db.session.commit()
    assert b'No students found' not in manager.get('/admin?view=students&access=active').data
    with app.app_context():
        db.session.add(TuitionControl(enrollment_id=eid,status='current',access_hold=True,revision=1));db.session.commit()
    assert b'No students found' in manager.get('/admin?view=students&access=active').data
    assert b'On hold' in manager.get('/admin?view=students&access=hold').data
    with app.app_context():db.session.add(StudentArchive(student_id=sid,reason='test archive'));db.session.commit()
    assert number.encode() not in manager.get('/admin?view=students').data
    assert number.encode() in manager.get('/admin?view=students&archived=yes').data
    assert manager.get('/admin?view=students&student='+sid).status_code==404
    panel=manager.get('/admin?view=students&archived=yes&student='+sid+'&tab=manage')
    assert panel.status_code==200 and b'Restore deleted record' in panel.data


def test_panel_real_data_and_csv_are_admin_only_and_formula_safe(app):
    manager,sid,number,eid,cid,cohort=fixture(app)
    with app.app_context():
        s=db.session.get(Student,sid);s.name='=1+1';db.session.commit()
    for tab in ('overview','payments','activity','manage'):
        panel=manager.get('/admin?view=students&student='+sid+'&tab='+tab)
        assert panel.status_code==200 and b'Student overview' in panel.data
    exported=manager.get('/admin/students/export.csv?id='+sid)
    rows=list(csv.reader(io.StringIO(exported.text)))
    assert len(rows)==2 and rows[1][0]==number and rows[1][1]=="'=1+1"
    assert exported.headers['Cache-Control']=='no-store'
    visitor=app.test_client()
    assert visitor.get('/admin/students/export.csv').status_code==302
    assert visitor.get('/admin?view=students&student='+sid).status_code==302
    assert b'student-panel-body' not in visitor.get('/admin?view=students&student='+sid).data


def test_pagination_and_export_preserve_search_scope(app):
    manager,sid,number,eid,cid,cohort=fixture(app)
    with app.app_context():
        for i in range(12):
            s=Student(name='Directory '+str(i),email=f'directory-{i}@example.test',phone='+14375550111',country='Canada',kind='new',password_hash='not-used-in-test');db.session.add(s);db.session.flush();db.session.add(StudentIdentity(student_id=s.id))
        db.session.commit()
    first=manager.get('/admin?view=students&q=Directory')
    second=manager.get('/admin?view=students&q=Directory&page=2')
    assert first.data.count(b'data-select-student')==6 and second.data.count(b'data-select-student')==6
    assert b'Showing 7' in second.data
    data=manager.get('/admin/students/export.csv?q=Directory').text
    assert len(list(csv.reader(io.StringIO(data))))==13 and number not in data


def test_multiple_programs_stay_one_student_and_new_receipts_need_review(app):
    from datetime import timedelta
    from app import Course,Cohort,PlanPaymentProof
    manager,sid,number,eid,cid,cohort=fixture(app)
    with app.app_context():
        e=db.session.get(Enrollment,eid);e.payment_status='verified';e.status='approved'
        c=db.session.scalar(select(Course).where(Course.slug=='devops'));batch=db.session.scalar(select(Cohort).where(Cohort.course_id==c.id))
        db.session.add(Enrollment(student_id=sid,course_id=c.id,cohort_id=batch.id,created_at=now()+timedelta(minutes=1)))
        db.session.add(PlanPaymentProof(enrollment_id=eid,stage=1,amount=10,currency='USD',reference='NEXT-INSTALLMENT',receipt_name='next.pdf',receipt_mime='application/pdf',status='submitted'));db.session.commit()
    page=manager.get('/admin?view=students')
    assert page.data.count(b'data-select-student')==1
    review=manager.get('/admin?view=students&payment=review')
    assert b'Review needed' in review.data and number.encode() in review.data
    payments=manager.get('/admin?view=students&student='+sid+'&tab=payments')
    assert b'NEXT-INSTALLMENT' in payments.data and b'next.pdf' in payments.data
    activity=manager.get('/admin?view=students&student='+sid+'&tab=activity')
    assert b'Payment receipt submitted' in activity.data
    for invalid in ('year=%C2%B2','year='+'9'*100,'program='+'9'*30,'page='+'9'*100,'tab=unknown'):
        assert manager.get('/admin?view=students&'+invalid).status_code==400
