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
        with self.module.database() as db:
            db.execute('DELETE FROM students')
            db.execute('DELETE FROM courses')
        self.client = self.module.app.test_client()

    def token(self, path='/login'):
        page = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', page)[1]

    def login(self):
        return self.client.post('/login', data=dict(csrf=self.token(), username='admin', password='test-password'))

    def post(self, **fields):
        return self.client.post('/admin', data=dict(csrf=self.token('/admin'), **fields), follow_redirects=True)

    def register_student(self, client, name, email, password='student-password'):
        page = client.get('/register').get_data(as_text=True)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page)[1]
        return client.post('/register', data=dict(csrf=csrf, name=name, email=email,
                          password=password, confirm_password=password), follow_redirects=True)

    def student_post(self, client, **fields):
        page = client.get('/portal').get_data(as_text=True)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page)[1]
        return client.post('/portal', data=dict(csrf=csrf, **fields), follow_redirects=True)

    def test_student_portals_and_admin_results(self):
        self.login()
        self.post(action='course', name='DevOps Project')
        alice = self.module.app.test_client()
        bob = self.module.app.test_client()
        self.assertIn(b'Your student portal is ready', self.register_student(alice, 'Alice', 'alice@example.com').data)
        self.register_student(bob, 'Bob', 'bob@example.com')
        self.student_post(alice, action='enroll', course_id='1')
        self.student_post(alice, action='submit', course_id='1', student_id='2', title='Alice Private Project',
                          project_url='https://github.com/example/alice', notes='Built with Ansible')
        self.assertIn(b'Alice Private Project', alice.get('/portal').data)
        self.assertNotIn(b'Alice Private Project', bob.get('/portal').data)
        self.assertEqual(bob.get('/admin').status_code, 302)
        self.assertEqual(alice.post('/admin', data={'action':'grade'}).status_code, 400)
        self.assertIn(b'Alice Private Project', self.client.get('/admin').data)
        self.post(action='grade', student_id='1', course_id='1', score='92', feedback='Excellent automation')
        page = alice.get('/portal').data
        self.assertIn(b'92/100', page)
        self.assertIn(b'Excellent automation', page)
        self.post(action='grade', student_id='1', course_id='1', score='101', feedback='Invalid')
        self.assertIn(b'92/100', alice.get('/portal').data)
        self.student_post(alice, action='submit', course_id='1', title='Revised Project',
                          project_url='https://github.com/example/alice', notes='Updated')
        self.assertIn(b'Awaiting review', alice.get('/portal').data)
        self.assertNotIn(b'92/100', alice.get('/portal').data)
        # Students cannot submit to an unenrolled course or set their own grades.
        self.assertEqual(self.student_post(bob, action='submit', course_id='1', title='Fake',
                         project_url='https://example.com', notes='').status_code, 403)
        self.assertEqual(self.student_post(alice, action='grade', course_id='1', score='100').status_code, 400)
        with self.module.database() as db:
            self.assertEqual(db.execute('SELECT student_id FROM submissions').fetchone()[0], 1)
            self.assertIsNone(db.execute('SELECT score FROM submissions').fetchone()[0])
            stored = db.execute('SELECT password_hash FROM student_accounts WHERE student_id=1').fetchone()[0]
            self.assertNotIn('student-password', stored)
        self.post(action='unenroll', student_id='1', course_id='1')
        with self.module.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM submissions').fetchone()[0], 0)

    def test_registration_login_and_existing_student_access(self):
        learner = self.module.app.test_client()
        self.assertEqual(learner.get('/portal').status_code, 302)
        self.assertEqual(learner.post('/register', data={}).status_code, 400)
        bad = self.register_student(learner, 'Invalid', 'bad-email')
        self.assertEqual(bad.status_code, 400)
        self.register_student(learner, 'New Student', 'new@example.com')
        duplicate = self.module.app.test_client()
        self.assertEqual(self.register_student(duplicate, 'Impersonator', 'new@example.com').status_code, 409)
        self.login()
        self.post(action='student', name='Existing Student', email='existing@example.com')
        self.assertEqual(self.register_student(duplicate, 'Impersonator', 'existing@example.com').status_code, 409)
        self.post(action='portal_password', student_id='2', password='temporary-password')
        page = duplicate.get('/student/login').get_data(as_text=True)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page)[1]
        bad_login = duplicate.post('/student/login', data=dict(csrf=csrf, email='existing@example.com', password='wrong'))
        self.assertIn(b'Invalid email or password', bad_login.data)
        good = duplicate.post('/student/login', data=dict(csrf=csrf, email='existing@example.com', password='temporary-password'), follow_redirects=True)
        self.assertIn(b'Hello, Existing Student', good.data)
        self.assertNotIn(b'Hello, New Student', good.data)
        self.post(action='portal_password', student_id='2', password='another-password')
        self.assertEqual(duplicate.get('/portal').status_code, 302)
        # Deleting and reusing a student id must not grant an old session access.
        with self.module.database() as db:
            db.execute('DELETE FROM students WHERE id=1')
            db.execute('INSERT INTO students VALUES(1, ?, ?)', ('Replacement', 'replacement@example.com'))
            db.execute('INSERT INTO student_accounts VALUES(1, ?)',
                       (self.module.generate_password_hash('new-password', method='pbkdf2:sha256:600000'),))
        self.assertEqual(learner.get('/portal').status_code, 302)

    def test_upgrade_preserves_existing_records(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for filename in ['config.json', 'secrets.json']:
                (base / filename).write_text((Path(self.tmp.name) / filename).read_text())
            with sqlite3.connect(base / 'school.sqlite3') as db:
                db.executescript("""CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE);
                    CREATE TABLE courses(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE);
                    CREATE TABLE enrollments(student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
                    course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,PRIMARY KEY(student_id,course_id));
                    INSERT INTO students VALUES(7,'Retained Student','retained@example.com');
                    INSERT INTO courses VALUES(8,'Retained Course'); INSERT INTO enrollments VALUES(7,8);""")
            env = {**os.environ, 'SCHOOL_CONFIG_DIR': temp, 'SCHOOL_DATA_DIR': temp,
                   'PYTHONDONTWRITEBYTECODE': '1'}
            for _ in range(2):
                subprocess.run([sys.executable, '-c', 'import app'], cwd=ROOT / 'roles/school_management/files/app',
                               env=env, check=True, capture_output=True)
            with sqlite3.connect(base / 'school.sqlite3') as db:
                self.assertEqual(db.execute('SELECT name FROM students WHERE id=7').fetchone()[0], 'Retained Student')
                self.assertEqual(db.execute('SELECT * FROM enrollments').fetchall(), [(7, 8)])
                self.assertEqual(db.execute('SELECT count(*) FROM student_accounts').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT count(*) FROM submissions').fetchone()[0], 0)

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
