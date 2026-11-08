"""LionTech classroom project: separate SQLite database on each managed host."""
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
from datetime import timedelta
from urllib.parse import urlencode, urlsplit
from werkzeug.security import generate_password_hash, check_password_hash
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
        CREATE TABLE IF NOT EXISTS student_accounts (
            student_id INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
            password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS submissions (
            student_id INTEGER NOT NULL, course_id INTEGER NOT NULL,
            title TEXT NOT NULL, project_url TEXT NOT NULL, notes TEXT NOT NULL,
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            score INTEGER CHECK(score BETWEEN 0 AND 100), feedback TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT,
            PRIMARY KEY(student_id, course_id),
            FOREIGN KEY(student_id, course_id) REFERENCES enrollments(student_id, course_id) ON DELETE CASCADE);

    ''')


@app.before_request
def protect_forms():
    if request.method == 'POST':
        expected = session.get('csrf')
        if not expected or not hmac.compare_digest(expected.encode(), request.form.get('csrf', '').encode()):
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
                elif action == 'portal_password':
                    password = request.form.get('password', '')
                    if not 10 <= len(password) <= 128:
                        raise ValueError('Use a password of 10–128 characters.')
                    db.execute('''INSERT INTO student_accounts VALUES(?,?)
                        ON CONFLICT(student_id) DO UPDATE SET password_hash=excluded.password_hash''',
                        (int(request.form['student_id']), generate_password_hash(password, method='pbkdf2:sha256:600000')))
                elif action == 'grade':
                    score = int(request.form['score'])
                    feedback = request.form.get('feedback', '').strip()
                    if not 0 <= score <= 100 or len(feedback) > 2000:
                        raise ValueError('Invalid result.')
                    result = db.execute('''UPDATE submissions SET score=?, feedback=?, reviewed_at=CURRENT_TIMESTAMP
                        WHERE student_id=? AND course_id=?''',
                        (score, feedback, int(request.form['student_id']), int(request.form['course_id'])))
                    if result.rowcount != 1:
                        raise ValueError('Submission no longer exists.')
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
        submissions = db.execute('''SELECT r.*, s.name student, s.email, c.name course
            FROM submissions r JOIN students s ON s.id=r.student_id
            JOIN courses c ON c.id=r.course_id ORDER BY r.submitted_at DESC''').fetchall()
        accounts = {row['student_id'] for row in db.execute('SELECT student_id FROM student_accounts')}
    return render_template('dashboard.html', students=students, courses=courses,
                           enrollments=enrollments, submissions=submissions, accounts=accounts)


def student_session_token(password_hash):
    return hmac.new(app.secret_key.encode(), password_hash.encode(), 'sha256').hexdigest()


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if (not 1 <= len(name) <= 100 or len(email) > 254 or '@' not in email
                or '.' not in email.rsplit('@', 1)[1] or not 10 <= len(password) <= 128
                or password != request.form.get('confirm_password')):
            flash('Enter your name, a valid email, and matching passwords of 10–128 characters.')
            return render_template('register.html'), 400
        password_hash = generate_password_hash(password, method='pbkdf2:sha256:600000')
        try:
            with database() as db:
                student_id = db.execute('INSERT INTO students(name,email) VALUES(?,?)', (name, email)).lastrowid
                db.execute('INSERT INTO student_accounts VALUES(?,?)',
                           (student_id, password_hash))
        except sqlite3.IntegrityError:
            flash('This email is already registered. Sign in or ask your administrator to enable/reset your portal.')
            return render_template('register.html'), 409
        session.clear()
        session['student_id'] = student_id
        session['student_auth'] = student_session_token(password_hash)
        session.permanent = True
        flash('Your student portal is ready. Choose a course to get started.')
        return redirect(url_for('portal'))
    return render_template('register.html')


@app.route('/student/login', methods=['GET', 'POST'])
def student_login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        with database() as db:
            account = db.execute('''SELECT a.student_id, a.password_hash FROM student_accounts a
                JOIN students s ON s.id=a.student_id WHERE s.email=?''', (email,)).fetchone()
        # Bound input size before the password KDF; generic errors do not reveal account state.
        if account and len(password) <= 128 and check_password_hash(account['password_hash'], password):
            session.clear()
            session['student_id'] = account['student_id']
            session['student_auth'] = student_session_token(account['password_hash'])
            session.permanent = True
            return redirect(url_for('portal'))
        flash('Invalid email or password.')
    return render_template('student_login.html')


@app.route('/portal', methods=['GET', 'POST'])
def portal():
    student_id = session.get('student_id')
    if not student_id:
        return redirect(url_for('student_login'))
    with database() as db:
        student = db.execute('''SELECT s.*, a.password_hash FROM students s JOIN student_accounts a ON a.student_id=s.id
            WHERE s.id=?''', (student_id,)).fetchone()
        if not student or not hmac.compare_digest(session.get('student_auth', '').encode(),
                                                   student_session_token(student['password_hash']).encode()):
            session.clear()
            return redirect(url_for('student_login'))
        if request.method == 'POST':
            try:
                course_id = int(request.form['course_id'])
                if request.form.get('action') == 'enroll':
                    db.execute('INSERT INTO enrollments VALUES(?,?)', (student_id, course_id))
                    flash('You are enrolled. Submit your project when it is ready.')
                elif request.form.get('action') == 'submit':
                    title = request.form.get('title', '').strip()
                    project_url = request.form.get('project_url', '').strip()
                    notes = request.form.get('notes', '').strip()
                    url = urlsplit(project_url)
                    if (not 1 <= len(title) <= 150 or len(project_url) > 1000 or len(notes) > 2000
                            or url.scheme not in ('http', 'https') or not url.netloc):
                        raise ValueError('Invalid project details.')
                    if not db.execute('SELECT 1 FROM enrollments WHERE student_id=? AND course_id=?',
                                      (student_id, course_id)).fetchone():
                        abort(403)
                    db.execute('''INSERT INTO submissions(student_id,course_id,title,project_url,notes) VALUES(?,?,?,?,?)
                        ON CONFLICT(student_id,course_id) DO UPDATE SET title=excluded.title,
                        project_url=excluded.project_url, notes=excluded.notes, submitted_at=CURRENT_TIMESTAMP,
                        score=NULL, feedback='', reviewed_at=NULL''', (student_id, course_id, title, project_url, notes))
                    flash('Project submitted. Your administrator can now review your result.')
                else:
                    abort(400)
            except (ValueError, KeyError):
                flash('Check the required fields. Use an http(s) project link and respect the field limits.')
            except sqlite3.IntegrityError:
                flash('Already enrolled, or the course no longer exists.')
            return redirect(url_for('portal'))
        available = db.execute('''SELECT * FROM courses WHERE id NOT IN
            (SELECT course_id FROM enrollments WHERE student_id=?) ORDER BY name''', (student_id,)).fetchall()
        enrolled = db.execute('''SELECT c.id, c.name, r.title, r.project_url, r.notes,
            r.score, r.feedback, r.submitted_at, r.reviewed_at FROM enrollments e
            JOIN courses c ON c.id=e.course_id LEFT JOIN submissions r
            ON r.student_id=e.student_id AND r.course_id=e.course_id
            WHERE e.student_id=? ORDER BY c.name''', (student_id,)).fetchall()
    return render_template('portal.html', student=student, available=available, enrolled=enrolled)
