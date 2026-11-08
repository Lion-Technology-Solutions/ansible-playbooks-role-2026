#!/usr/bin/env python3
"""Functional app tests with temporary configuration and SQLite storage."""
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import sys
import socket
import subprocess
import time
from urllib.request import urlopen

sys.dont_write_bytecode = True
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SchoolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        (base / 'config.json').write_text(json.dumps(dict(brand='LionTech Academy', phone='+1 (647) 381-8836', whatsapp='16473818836', website='https://www.liontechacademy.com', completion_message='Project completed')))
        (base / 'secrets.json').write_text(json.dumps(dict(password='test-password', secret_key='test-session-key')))
        os.environ['SCHOOL_CONFIG_DIR'] = str(base)
        os.environ['SCHOOL_DATA_DIR'] = str(base)
        spec = importlib.util.spec_from_file_location('school_app', ROOT / 'roles/school_management/files/app/app.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.testing = True

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.client = self.module.app.test_client()

    def token(self, path='/login'):
        page = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', page)[1]

    def login(self):
        return self.client.post('/login', data=dict(csrf=self.token(), username='admin', password='test-password'))

    def post(self, **fields):
        return self.client.post('/admin', data=dict(csrf=self.token('/admin'), **fields), follow_redirects=True)

    def test_waitress_http_server(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, '-m', 'waitress', f'--listen=127.0.0.1:{port}', 'app:app'],
            cwd=ROOT / 'roles/school_management/files/app',
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for attempt in range(40):
                try:
                    with urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                        self.assertEqual(json.load(response), {'status': 'ok'})
                    break
                except OSError:
                    if process.poll() is not None:
                        self.fail('Waitress exited before becoming healthy')
                    time.sleep(0.1)
            else:
                self.fail('Waitress health check timed out')
            with urlopen(f'http://127.0.0.1:{port}/') as response:
                self.assertIn(b'Project+completed', response.read())
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_public_portal_and_whatsapp(self):
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'https://wa.me/16473818836?text=Project+completed', page.data)
        self.assertIn(b'LionTech Academy', page.data)
        self.assertEqual(self.client.get('/health').json, {'status': 'ok'})
        self.assertEqual(self.client.get('/admin').status_code, 302)

    def test_authentication_and_csrf(self):
        self.assertEqual(self.client.post('/login', data={}).status_code, 400)
        bad = self.client.post('/login', data=dict(csrf=self.token(), username='admin', password='wrong'))
        self.assertIn(b'Invalid username or password', bad.data)
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.post('/admin', data={'action':'course','name':'Unauthorized'}).status_code, 400)
        self.assertEqual(self.client.get('/admin').status_code, 200)
        self.client.post('/logout', data=dict(csrf=self.token('/admin')))
        self.assertEqual(self.client.get('/admin').status_code, 302)

    def test_persistence_enrollment_validation_and_deletion(self):
        self.login()
        self.post(action='student', name='<script>alert(1)</script>', email='learner@example.com')
        self.post(action='course', name='Ansible Automation')
        self.assertIn(b'Record already exists', self.post(action='student', name='Duplicate', email='learner@example.com').data)
        self.post(action='enroll', student_id='1', course_id='1')
        page = self.client.get('/admin').data
        self.assertIn(b'&lt;script&gt;', page)
        self.assertNotIn(b'<script>', page)
        self.assertIn(b'Ansible Automation', page)
        with self.module.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM enrollments').fetchone()[0], 1)
        other_browser = self.module.app.test_client()
        with other_browser.session_transaction() as session:
            session['admin'] = True
        self.assertIn(b'learner@example.com', other_browser.get('/admin').data)
        self.post(action='delete_student', id='1')
        with self.module.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM enrollments').fetchone()[0], 0)
        self.post(action='delete_course', id='1')
        self.assertIn(b'Check the required fields', self.post(action='student', name='Bad', email='invalid').data)


if __name__ == '__main__':
    unittest.main(verbosity=2)
