import os
from pathlib import Path
import sys
import pytest

os.environ.update(SCHOOL_TESTING='1', DATABASE_URL='sqlite:///:memory:',
                  SCHOOL_SECRET_KEY='test-session-secret', SCHOOL_ADMIN_PASSWORD='test-admin-password')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app, db, AuthPolicy


@pytest.fixture
def app():
    application = create_app({'TESTING': True, 'DATA_ANALYTICS_LINK_PLAN':False, 'SESSION_COOKIE_SECURE': False,'WHATSAPP_TEMPLATE_VALIDATOR':lambda *args:{'type':'twilio/text','approval':'approved'}})
    with application.app_context():
        db.create_all()
        db.session.add(AuthPolicy(id=1,require_email=False,require_mfa=False));db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()
