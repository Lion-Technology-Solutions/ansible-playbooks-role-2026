import os
from pathlib import Path
import sys
import pytest

os.environ.update(SCHOOL_TESTING='1', DATABASE_URL='sqlite:///:memory:',
                  SCHOOL_SECRET_KEY='test-session-secret', SCHOOL_ADMIN_PASSWORD='test-admin-password')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app, db


@pytest.fixture
def app():
    application = create_app({'TESTING': True, 'SESSION_COOKIE_SECURE': False})
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()
