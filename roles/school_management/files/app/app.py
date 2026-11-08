"""LionTech classroom project: separate SQLite database on each managed host."""
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
from datetime import timedelta
from urllib.parse import urlencode
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

CONFIG_DIR = Path(os.environ.get('SCHOOL_CONFIG_DIR', '/etc/liontech-school'))
DATA_DIR = Path(os.environ.get('SCHOOL_DATA_DIR', '/var/lib/liontech-school'))
branding = json.loads((CONFIG_DIR / 'config.json').read_text())
credentials = json.loads((CONFIG_DIR / 'secrets.json').read_text())
app = Flask(__name__, root_path=str(Path(__file__).resolve().parent))
app.config.update(SECRET_KEY=credentials['secret_key'], MAX_CONTENT_LENGTH=16384,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=branding.get('secure_cookie', False),
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=2))


def database():
    conn = sqlite3.connect(DATA_DIR / 'school.sqlite3', timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


with database() as db:
    db.executescript('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS enrollments (
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
            PRIMARY KEY(student_id, course_id));
    ''')


@app.before_request
def protect_forms():
    if request.method == 'POST':
        expected = session.get('csrf')
        if not expected or not hmac.compare_digest(expected, request.form.get('csrf', '')):
            abort(400, 'Form expired. Reload the page and try again.')


@app.after_request
def headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.context_processor
def context():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_hex(24)
    return dict(brand=branding, csrf=session['csrf'],
                whatsapp_url='https://wa.me/' + branding['whatsapp'] + '?' + urlencode({'text': branding['completion_message']}))


@app.get('/health')
def health():
    with database() as db:
        db.execute('SELECT count(*) FROM students').fetchone()
    return {'status': 'ok'}


@app.get('/')
def home():
    return render_template('home.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        valid_user = hmac.compare_digest(request.form.get('username', '').encode(), b'admin')
        valid_password = hmac.compare_digest(request.form.get('password', '').encode(), credentials['password'].encode())
        if valid_user and valid_password:
            session.clear()
            session['admin'] = True
            session.permanent = True
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.')
    return render_template('login.html')


@app.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


@app.route('/admin', methods=['GET', 'POST'])
def dashboard():
    if not session.get('admin'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        try:
            with database() as db:
                action = request.form.get('action')
                name = request.form.get('name', '').strip()
                if action in ('student', 'course') and not 1 <= len(name) <= 100:
                    raise ValueError('Enter a name of 1–100 characters.')
                if action == 'student':
                    email = request.form.get('email', '').strip().lower()
                    if len(email) > 254 or '@' not in email or '.' not in email.rsplit('@', 1)[1]:
                        raise ValueError('Enter a valid email address.')
                    db.execute('INSERT INTO students(name,email) VALUES(?,?)', (name, email))
                elif action == 'course':
                    db.execute('INSERT INTO courses(name) VALUES(?)', (name,))
                elif action == 'enroll':
                    db.execute('INSERT INTO enrollments VALUES(?,?)', (int(request.form['student_id']), int(request.form['course_id'])))
                elif action == 'delete_student':
                    db.execute('DELETE FROM students WHERE id=?', (int(request.form['id']),))
                elif action == 'delete_course':
                    db.execute('DELETE FROM courses WHERE id=?', (int(request.form['id']),))
                elif action == 'unenroll':
                    db.execute('DELETE FROM enrollments WHERE student_id=? AND course_id=?', (int(request.form['student_id']), int(request.form['course_id'])))
                else:
                    abort(400)
            flash('School records updated.')
        except (ValueError, KeyError):
            flash('Check the required fields and try again.')
        except sqlite3.IntegrityError:
            flash('Record already exists, or the selected student/course no longer exists.')
        return redirect(url_for('dashboard'))
    with database() as db:
        students = db.execute('SELECT * FROM students ORDER BY name').fetchall()
        courses = db.execute('SELECT * FROM courses ORDER BY name').fetchall()
        enrollments = db.execute('''SELECT e.student_id, e.course_id, s.name student, c.name course
            FROM enrollments e JOIN students s ON s.id=e.student_id
            JOIN courses c ON c.id=e.course_id ORDER BY s.name,c.name''').fetchall()
    return render_template('dashboard.html', students=students, courses=courses, enrollments=enrollments)
