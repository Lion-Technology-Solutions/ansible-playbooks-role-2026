from datetime import date, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import select
from app import (db, Student, Enrollment, Course, CoursePrice, BillingSelection, PaymentEntry,
                 Lesson, LessonCompletion, Certificate, StudentPreference, now)
from experience import installment_schedule, money_value, csv_safe
from test_portal import admin, analytics, approve, csrf, post, register, request_enrollment


def setup(app):
    manager,student=app.test_client(),app.test_client()
    admin(manager);register(student)
    course,cohort=analytics(app)
    request_enrollment(student,course,cohort)
    with app.app_context(): eid=db.session.scalar(select(Enrollment.id))
    return manager,student,course,cohort,eid


def price(manager,course,**extra):
    values=dict(action='course_price',course_id=course,country='Cameroon',currency='EUR',total='100.00',installments='3',full_url='https://example.com/full',installment_url='https://example.com/monthly')
    values.update(extra)
    return post(manager,'/admin',**values)


def test_guided_enrollment_and_ownership(app):
    student=app.test_client();student.get('/apply')
    course,cohort=analytics(app)
    token=csrf(student,'/apply')
    assert student.post('/apply/choose',data={'csrf':token,'course_id':'bad','cohort_id':cohort}).status_code==400
    response=student.post('/apply/choose',data={'csrf':token,'course_id':course,'cohort_id':cohort})
    assert response.location.endswith('/register')
    page=register(student,country='Spain')
    assert page.status_code==200 and b'Your payments' in page.data
    with app.app_context():
        e=db.session.scalar(select(Enrollment));eid=e.id
        assert e.cohort_id==cohort and e.student.approved
        assert db.session.get(StudentPreference,e.student_id).timezone=='Europe/Madrid'
    other=app.test_client();register(other,'other@example.com')
    assert other.get(f'/apply/payment/{eid}').status_code==403
    assert post(other,f'/enrollment/{eid}/plan',plan='full').status_code==403
    # Choosing twice must reuse the existing enrollment.
    for _ in range(2):student.post('/apply/choose',data={'csrf':csrf(student,'/apply'),'course_id':course,'cohort_id':cohort})
    with app.app_context(): assert len(list(db.session.scalars(select(Enrollment))))==1


def test_billing_snapshot_ledger_currency_and_retries(app):
    manager,student,course,cohort,eid=setup(app)
    price(manager,course)
    page=post(student,f'/enrollment/{eid}/plan',plan='installments')
    assert b'USD' not in page.data and b'example.com/monthly' not in page.data
    price(manager,course,total='200.00',installment_url='https://example.com/changed')
    page=student.get(f'/apply/payment/{eid}')
    assert b'example.com/monthly' not in page.data
    with app.app_context():
        terms=db.session.get(BillingSelection,eid)
        assert terms.total==Decimal('100.00') and terms.installments==3
    values=dict(action='record_payment',enrollment_id=eid,reference='REAL-TXN',currency='EUR',amount='33.33',received_at='')
    for _ in range(2):post(manager,'/admin',**values)
    with app.app_context():
        assert len(list(db.session.scalars(select(PaymentEntry))))==1
        assert db.session.get(Enrollment,eid).status=='pending'
    assert b'already recorded with different details' in post(manager,'/admin',**{**values,'amount':'30.00'}).data
    assert b'currency saved' in post(manager,'/admin',**{**values,'reference':'WRONG-CURRENCY','currency':'USD'}).data
    assert b'change a plan after payment' in post(student,f'/enrollment/{eid}/plan',plan='full').data
    page=student.get(f'/apply/payment/{eid}')
    assert b'USD' not in page.data
    page=manager.get('/admin?currency=EUR')
    assert b'33.33' in page.data and b'66.67' in page.data and b'100.00' in page.data
    assert b'33.33' not in manager.get('/admin?currency=USD').data
    assert b'33.33' not in manager.get('/admin?country=Spain&currency=EUR').data
    report=manager.get('/admin/reports/payments.csv?currency=EUR')
    assert report.status_code==200 and b'REAL-TXN,EUR,33.33' in report.data
    assert student.get('/admin/reports/payments.csv').status_code==302
    post(manager,'/admin',action='approve',enrollment_id=eid)
    assert b'Enrollment active' in student.get(f'/apply/payment/{eid}').data


def test_completion_progress_and_certificate_gates(app):
    manager,student,course,cohort,eid=setup(app)
    with app.app_context():
        items=[Lesson(course_id=course,title=title,published=True,position=i,available_at=now()) for i,title in enumerate(['First lesson','Second lesson'],1)]
        items.append(Lesson(course_id=course,title='Future lesson',published=True,position=3,available_at=now()+timedelta(days=2)))
        db.session.add_all(items);db.session.commit();ids=[item.id for item in items]
    assert b'First lesson' not in student.get('/portal').data
    assert post(student,f'/lessons/{ids[0]}/complete').status_code==403
    post(student,f'/enrollment/{eid}/payment',reference='COMPLETE-PAID')
    post(manager,'/admin',action='verify_and_approve',enrollment_id=eid)
    assert b'completion of all released lessons' in post(manager,'/admin',action='issue_certificate',enrollment_id=eid).data
    for _ in range(2):post(student,f'/lessons/{ids[0]}/complete')
    assert b'50%' in student.get('/portal').data
    assert post(student,f'/lessons/{ids[2]}/complete').status_code==403
    post(student,f'/lessons/{ids[1]}/complete')
    assert b'100%' in student.get('/portal').data
    for _ in range(2):post(manager,'/admin',action='issue_certificate',enrollment_id=eid)
    with app.app_context():
        assert len(list(db.session.scalars(select(LessonCompletion))))==2
        certs=list(db.session.scalars(select(Certificate)));assert len(certs)==1;cid=certs[0].id
    assert student.get(f'/certificates/{cid}').status_code==200
    assert manager.get(f'/certificates/{cid}').status_code==200
    assert app.test_client().get(f'/certificates/{cid}').status_code==403
    other=app.test_client();register(other,'certificate-other@example.com')
    assert other.get(f'/certificates/{cid}').status_code==403
    post(manager,'/admin',action='reject',enrollment_id=eid)
    assert post(student,f'/lessons/{ids[0]}/complete').status_code==403


def test_preferences_admin_sections_and_filters(app):
    manager,student,course,cohort,eid=setup(app)
    response=student.post('/preferences',data={'csrf':csrf(student,'/portal'),'language':'es','timezone':'Europe/Madrid','next':'/portal'})
    assert response.location=='/portal'
    page=student.get('/portal');assert b'lang="es"' in page.data and b'Mis programas' in page.data and b'data-timezone="Europe/Madrid"' in page.data
    response=student.post('/preferences',data={'csrf':csrf(student,'/portal'),'language':'en','timezone':'UTC','next':'//evil.example'})
    assert response.location=='/portal'
    assert student.post('/preferences',data={'csrf':csrf(student,'/portal'),'language':'xx','timezone':'UTC'}).status_code==400
    for view in ['students','enrollments','payments','programs','classes','materials','announcements','reports','settings']:
        assert manager.get('/admin?view='+view).status_code==200
    for query in ['currency=BAD','program=bad','country=bad','year=9999','page=bad']:
        assert manager.get('/admin?'+query).status_code==400
    assert b'5,000' in manager.get('/admin').data


def test_financial_helpers_rounding_dates_and_invalid_amounts():
    schedule=installment_schedule(Decimal('100'),3,date(2027,1,31))
    assert [item['due'] for item in schedule]==[date(2027,1,31),date(2027,2,28),date(2027,3,31)]
    assert [item['amount'] for item in schedule]==[Decimal('33.33'),Decimal('33.33'),Decimal('33.34')]
    tiny=installment_schedule(Decimal('.36'),24,date(2027,1,1))
    assert all(item['amount']>0 for item in tiny) and sum(item['amount'] for item in tiny)==Decimal('.36')
    for value in ['NaN','Infinity','-1','0','1.234','9999999999','bad']:
        with pytest.raises(ValueError):money_value(value)
    assert csv_safe('=HYPERLINK("bad")').startswith("'=")
    assert csv_safe('Normal')=='Normal'
