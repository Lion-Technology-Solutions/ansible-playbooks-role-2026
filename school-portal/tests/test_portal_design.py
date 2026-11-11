"""Dashboard reminders reflect account policy and real enrollment state."""
from datetime import timedelta
from sqlalchemy import select
from app import db, AuthPolicy, AuthProfile, AuthFactor, Student, aware
from test_portal import register


def test_security_reminder_uses_activation_and_hides_after_setup(app):
    client = app.test_client()
    register(client)
    with app.app_context():
        student = db.session.scalar(select(Student))
        profile = db.session.get(AuthProfile, student.id)
        policy = db.session.get(AuthPolicy, 1)
        policy.require_mfa = True
        policy.grace_days = 7
        deadline = aware(profile.activated_at) + timedelta(days=7)
        sid = student.id
        db.session.commit()
    page = client.get('/portal')
    assert page.status_code == 200
    assert b'Account security reminder' in page.data
    assert deadline.isoformat().encode() in page.data
    with app.app_context():
        db.session.add(AuthFactor(student_id=sid, method='totp', ciphertext='fixture'))
        db.session.commit()
    assert b'Account security reminder' not in client.get('/portal').data


def test_unenrolled_dashboard_does_not_claim_progress_or_payment(app):
    client = app.test_client()
    register(client)
    page = client.get('/portal')
    assert page.status_code == 200
    assert b'Your payment options and due dates appear when you select a program.' in page.data
    assert b'aria-label="Lesson completion"' not in page.data
    assert b'Class access active' not in page.data
