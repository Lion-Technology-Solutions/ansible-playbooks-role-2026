"""LionTech Academy portal. Durable Postgres on Vercel; explicit SQLite for tests only."""
import hashlib
import hmac
import os
import re
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from werkzeug.security import check_password_hash, generate_password_hash

UTC = timezone.utc
db = SQLAlchemy()
SUPPORT = '14375756656'
PAYMENTS = {
    'Cameroon': ('https://flutterwave.com/pay/zx0r0urssqpb', 'Pay 2,000 FCFA toward your first installment'),
    'Nigeria': ('https://flutterwave.com/pay/7a1b6zlid7fr', 'Pay your enrollment fee'),
    'Ghana': ('https://flutterwave.com/pay/med3t2upqqyt', 'Pay your enrollment fee'),
}
COURSES = [
    ('devops', 'DevOps', 'Build, automate, and deploy with confidence.', 'CI/CD · Linux · Docker · Kubernetes'),
    ('cybersecurity', 'Cybersecurity', 'Learn to defend systems and investigate threats.', 'Network security · Threats · Ethical hacking'),
    ('cloud-computing', 'Cloud Computing', 'Design and operate reliable cloud infrastructure.', 'AWS · Networking · Infrastructure as code'),
    ('data-analytics', 'Data Analytics', 'Turn raw data into decisions that matter.', 'Excel · SQL · Python · Power BI'),
]
COUNTRIES = ['Cameroon', 'Nigeria', 'Ghana', 'Canada', 'United States', 'United Kingdom', 'Other']


def now():
    return datetime.now(UTC)


class Student(db.Model):
    __tablename__ = 'portal_students'
    id = db.Column(db.String(36), primary_key=True, default=lambda: secrets.token_hex(16))
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    country = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(12), nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    approved = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    identity = db.relationship('StudentIdentity', back_populates='student', uselist=False)

    @property
    def student_number(self):
        return f'LT-{self.created_at.year}-{self.identity.id:06d}' if self.identity else 'Pending number'


class StudentIdentity(db.Model):
    """Additive table: existing student rows never need a destructive migration."""
    __tablename__ = 'portal_student_identities'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(36), db.ForeignKey('portal_students.id'), unique=True, nullable=False)
    student = db.relationship(Student, back_populates='identity')


class Course(db.Model):
    __tablename__ = 'portal_courses'
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(60), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    skills = db.Column(db.Text, nullable=False)
    registration_url = db.Column(db.Text, default='', nullable=False)
    resources_url = db.Column(db.Text, default='', nullable=False)
    profile = db.relationship('ProgramProfile', uselist=False)
    publication = db.relationship('CoursePublication', uselist=False)


class CoursePublication(db.Model):
    __tablename__ = 'portal_course_publications'
    course_id = db.Column(db.Integer, db.ForeignKey('portal_courses.id'), primary_key=True)
    published = db.Column(db.Boolean, default=False, nullable=False)
    published_at = db.Column(db.DateTime(timezone=True))
    payment_links = db.Column(db.JSON, default=dict, nullable=False)


class PublicUpdate(db.Model):
    __tablename__ = 'portal_public_updates'
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(60), unique=True, nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    summary = db.Column(db.String(500), nullable=False)
    body = db.Column(db.Text, nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True))
    ends_at = db.Column(db.DateTime(timezone=True))
    location = db.Column(db.String(250), default='', nullable=False)
    registration_url = db.Column(db.Text, default='', nullable=False)
    published = db.Column(db.Boolean, default=False, nullable=False)
    publish_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)


class ProgramProfile(db.Model):
    __tablename__ = 'portal_program_profiles'
    course_id = db.Column(db.Integer, db.ForeignKey('portal_courses.id'), primary_key=True)
    duration = db.Column(db.String(150), default='', nullable=False)
    schedule = db.Column(db.String(500), default='', nullable=False)
    curriculum = db.Column(db.Text, default='', nullable=False)
    tuition = db.Column(db.JSON, default=dict, nullable=False)


class Cohort(db.Model):
    __tablename__ = 'portal_cohorts'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('portal_courses.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    label = db.Column(db.String(120), nullable=False)
    __table_args__ = (UniqueConstraint('course_id', 'start_date'),)
    course = db.relationship(Course)


class Enrollment(db.Model):
    __tablename__ = 'portal_enrollments'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(36), db.ForeignKey('portal_students.id'), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('portal_courses.id'), nullable=False)
    cohort_id = db.Column(db.Integer, db.ForeignKey('portal_cohorts.id'))
    status = db.Column(db.String(20), default='pending', nullable=False)
    payment_status = db.Column(db.String(20), default='unpaid', nullable=False)
    payment_reference = db.Column(db.String(150), unique=True)
    payment_note = db.Column(db.Text, default='', nullable=False)
    verified_at = db.Column(db.DateTime(timezone=True))
    approved_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint('student_id', 'course_id'),)
    student = db.relationship(Student)
    course = db.relationship(Course)
    cohort = db.relationship(Cohort)


class ClassSession(db.Model):
    __tablename__ = 'portal_classes'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('portal_courses.id'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False)
    registration_url = db.Column(db.Text, default='', nullable=False)
    course = db.relationship(Course)


class ClassRegistration(db.Model):
    __tablename__ = 'portal_class_registrations'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(36), db.ForeignKey('portal_students.id'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('portal_classes.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint('student_id', 'class_id'),)


class Recording(db.Model):
    __tablename__ = 'portal_recordings'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('portal_courses.id'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    url = db.Column(db.Text, nullable=False)
    available_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    seed_key = db.Column(db.String(80), unique=True)
    course = db.relationship(Course)


class Notification(db.Model):
    __tablename__ = 'portal_notifications'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(36), db.ForeignKey('portal_students.id'), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    body = db.Column(db.Text, default='', nullable=False)
    available_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    read_at = db.Column(db.DateTime(timezone=True))
    recording_id = db.Column(db.Integer, db.ForeignKey('portal_recordings.id'))
    __table_args__ = (UniqueConstraint('student_id', 'recording_id'),)


class Attendance(db.Model):
    __tablename__ = 'portal_attendance'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(36), db.ForeignKey('portal_students.id'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('portal_classes.id'), nullable=False)
    submitted_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint('student_id', 'class_id'),)
    student = db.relationship(Student)
    lesson = db.relationship(ClassSession)


class Assessment(db.Model):
    __tablename__ = 'portal_assessments'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('portal_courses.id'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    kind = db.Column(db.String(12), nullable=False)
    instructions = db.Column(db.Text, nullable=False)
    due_at = db.Column(db.DateTime(timezone=True))
    course = db.relationship(Course)


class Submission(db.Model):
    __tablename__ = 'portal_submissions'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(36), db.ForeignKey('portal_students.id'), nullable=False)
    assessment_id = db.Column(db.Integer, db.ForeignKey('portal_assessments.id'), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    url = db.Column(db.Text, default='', nullable=False)
    score = db.Column(db.Integer)
    feedback = db.Column(db.Text, default='', nullable=False)
    submitted_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    reviewed_at = db.Column(db.DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('student_id', 'assessment_id'), db.CheckConstraint('score >= 0 AND score <= 100'))
    student = db.relationship(Student)
    assessment = db.relationship(Assessment)


class LoginAttempt(db.Model):
    __tablename__ = 'portal_login_attempts'
    key = db.Column(db.String(64), primary_key=True)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    last_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)


def seed(connection):
    """Idempotent bootstrap under the schema transaction/advisory lock."""
    table = Course.__table__
    for slug, name, description, skills in COURSES:
        if not connection.execute(select(table.c.id).where(table.c.slug == slug)).first():
            connection.execute(table.insert().values(slug=slug, name=name, description=description, skills=skills,
                registration_url='https://us06web.zoom.us/meeting/register/yBAAj2k5TM2lEqpMXZY3ZQ' if slug == 'data-analytics' else '',
                resources_url='https://drive.google.com/drive/folders/186T95HhFghqKXATdxzXvvu43OWSmBFL2?usp=sharing' if slug == 'data-analytics' else ''))
    analytics = connection.execute(select(table.c.id).where(table.c.slug == 'data-analytics')).scalar_one()
    profile = ProgramProfile.__table__
    for course in connection.execute(select(table)).mappings():
        if not connection.execute(select(profile.c.course_id).where(profile.c.course_id == course['id'])).first():
            is_analytics = course['slug'] == 'data-analytics'
            connection.execute(profile.insert().values(course_id=course['id'], duration='3 months' if is_analytics else '',
                schedule='', curriculum='\n'.join(course['skills'].split(' · ')), tuition={
                    'Cameroon': '10,000 FCFA per month · 30,000 FCFA total over 3 months. Start with 2,000 FCFA toward your first installment; pay the remaining 8,000 FCFA after your first class.',
                    'Nigeria': '₦19,900 per month for 3 months. Enrollment fee: ₦2,500.',
                    'Ghana': 'Approximately GH₵198 per month · GH₵594 total over 3 months. Start with GH₵20 toward your first installment. Estimates may vary with exchange rates.',
                } if is_analytics else {}))
        publication = CoursePublication.__table__
        if not connection.execute(select(publication.c.course_id).where(publication.c.course_id == course['id'])).first():
            connection.execute(publication.insert().values(course_id=course['id'], published=True,
                published_at=now(), payment_links={country: value[0] for country, value in PAYMENTS.items()}))
    identities = StudentIdentity.__table__
    # The caller holds the schema advisory lock, so parallel cold starts cannot
    # assign two numbers to an existing account. Issued numbers are never reused.
    for sid in connection.execute(select(Student.id).order_by(Student.created_at, Student.id)).scalars():
        if not connection.execute(select(identities.c.id).where(identities.c.student_id == sid)).first():
            connection.execute(identities.insert().values(student_id=sid))
    recording = os.environ.get('INITIAL_ANALYTICS_RECORDING_URL', '')
    if recording and not connection.execute(select(Recording.id).where(Recording.seed_key == 'initial-data-analytics')).first():
        connection.execute(Recording.__table__.insert().values(course_id=analytics, title='Latest Data Analytics class', url=recording,
            available_at=now(), created_at=now(), seed_key='initial-data-analytics'))


def aware(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def field(name, maximum=100, required=True):
    value = request.form.get(name, '').strip()
    if (required and not value) or len(value) > maximum:
        raise ValueError('Check the required fields and field limits.')
    return value


def valid_url(value, optional=False):
    if not value and optional:
        return ''
    parsed = urlsplit(value)
    if parsed.scheme not in ('https', 'http') or not parsed.netloc or len(value) > 3000:
        raise ValueError('Enter a valid https or http link.')
    return value


def utc_field(name, optional=False):
    value = field(name, 40, not optional)
    return datetime.fromisoformat(value).replace(tzinfo=UTC) if value else None


def whatsapp(message):
    return 'https://wa.me/' + SUPPORT + '?' + urlencode({'text': message})


def content_slug(value):
    slug = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    if not slug or len(slug) > 60:
        raise ValueError('Use a short URL label with letters, numbers and hyphens (up to 60 characters).')
    return slug


def published_courses():
    return select(Course).join(CoursePublication).where(CoursePublication.published.is_(True)).order_by(Course.id)


def public_updates():
    return select(PublicUpdate).where(PublicUpdate.published.is_(True), PublicUpdate.publish_at <= now())


def course_payment(course, country):
    link = course.publication.payment_links.get(country) or course.publication.payment_links.get('Other')
    if not link:
        return None
    label = PAYMENTS[country][1] if course.slug in {c[0] for c in COURSES} and country in PAYMENTS else 'Pay enrollment fee'
    return link, label


def academy_today():
    return now().astimezone(ZoneInfo('America/Toronto')).date()


def next_start_dates(today=None):
    """The next two future Saturday/Sunday starts, in the academy's timezone."""
    day = today or academy_today()
    dates = []
    while len(dates) < 2:
        day += timedelta(days=1)
        if day.weekday() in (5, 6):
            dates.append(day)
    return dates


def upcoming_cohorts():
    dates = next_start_dates()
    with db.engine.begin() as connection:
        if connection.dialect.name == 'postgresql':
            connection.execute(text('SELECT pg_advisory_xact_lock(73592027)'))
        table = Cohort.__table__
        for course_id in connection.execute(select(Course.id)).scalars():
            for start in dates:
                if not connection.execute(select(table.c.id).where(table.c.course_id == course_id, table.c.start_date == start)).first():
                    connection.execute(table.insert().values(course_id=course_id, start_date=start, label='Weekend intake'))
    return db.session.scalars(select(Cohort).where(Cohort.start_date.in_(dates)).order_by(Cohort.start_date)).all()


def create_app(config=None):
    app = Flask(__name__)
    testing = os.environ.get('SCHOOL_TESTING') == '1'
    database_url = os.environ.get('DATABASE_URL', '')
    if database_url.startswith(('postgres://', 'postgresql://')):
        database_url = re.sub(r'^postgres(?:ql)?://', 'postgresql+psycopg://', database_url)
    app.config.update(
        SECRET_KEY=os.environ.get('SCHOOL_SECRET_KEY'),
        SQLALCHEMY_DATABASE_URI=database_url or None,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={'poolclass': NullPool} if database_url.startswith('postgres') else {},
        SESSION_COOKIE_SECURE=not testing, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=4), MAX_CONTENT_LENGTH=65536,
        ADMIN_PASSWORD=os.environ.get('SCHOOL_ADMIN_PASSWORD', ''), TESTING=testing,
    )
    if config:
        app.config.update(config)
    if not app.config['SECRET_KEY'] or not app.config['ADMIN_PASSWORD'] or not app.config['SQLALCHEMY_DATABASE_URI']:
        raise RuntimeError('Configure DATABASE_URL, SCHOOL_SECRET_KEY and SCHOOL_ADMIN_PASSWORD before starting the portal.')
    if not app.config['TESTING'] and not app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql+psycopg://'):
        raise RuntimeError('The deployed portal requires persistent PostgreSQL storage.')
    db.init_app(app)
    schema_lock = threading.Lock()
    schema_ready = False

    @app.before_request
    def prepare():
        nonlocal schema_ready
        if request.endpoint == 'static':
            return
        if not schema_ready:
            with schema_lock:
                if not schema_ready:
                    with db.engine.begin() as connection:
                        if connection.dialect.name == 'postgresql':
                            connection.execute(text('SELECT pg_advisory_xact_lock(73592026)'))
                        db.metadata.create_all(bind=connection)
                        seed(connection)
                    schema_ready = True
        g.student = None
        student = db.session.get(Student, session.get('student_id')) if session.get('student_id') else None
        if student:
            token = hmac.new(app.secret_key.encode(), student.password_hash.encode(), 'sha256').hexdigest()
            if hmac.compare_digest(token.encode(), session.get('student_auth', '').encode()):
                g.student = student
            else:
                session.clear()
        if session.get('admin'):
            admin_token = hmac.new(app.secret_key.encode(), app.config['ADMIN_PASSWORD'].encode(), 'sha256').hexdigest()
            if not hmac.compare_digest(admin_token.encode(), session.get('admin_auth', '').encode()):
                session.clear()
        if request.method == 'POST':
            token = session.get('csrf', '')
            if not token or not hmac.compare_digest(token.encode(), request.form.get('csrf', '').encode()):
                abort(400, 'Your form expired. Reload the page and try again.')

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.context_processor
    def context():
        session.setdefault('csrf', secrets.token_hex(24))
        account_request_url = whatsapp(f'LionTech student account creation request\nStudent number: {g.student.student_number}\nName: {g.student.name}\nStudent type: {g.student.kind}\nPlease review my account in the administrator dashboard.') if g.student else ''
        return dict(csrf=session['csrf'], support_url=whatsapp('Hello LionTech Academy, I need help with my student portal.'),
                    completion_url=whatsapp('Project completed'), account_request_url=account_request_url,
                    countries=COUNTRIES, payments=PAYMENTS, course_payment=course_payment, utcnow=now(), aware=aware)

    def student_required(approved=False):
        def decorate(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if not g.student:
                    return redirect(url_for('login'))
                if approved and not g.student.approved:
                    flash('Your portal is awaiting payment verification and administrator approval.')
                    return redirect(url_for('enrollments'))
                return fn(*args, **kwargs)
            return wrapped
        return decorate

    def admin_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get('admin'):
                return redirect(url_for('admin_login'))
            return fn(*args, **kwargs)
        return wrapped

    def enrollment_for(course_id):
        return db.session.scalar(select(Enrollment).where(Enrollment.student_id == g.student.id,
            Enrollment.course_id == course_id, Enrollment.status == 'approved', Enrollment.payment_status == 'verified'))

    def notify(student_id, title, body='', available_at=None, recording_id=None):
        db.session.add(Notification(student_id=student_id, title=title, body=body,
            available_at=available_at or now(), recording_id=recording_id))

    @app.get('/health')
    def health():
        db.session.execute(text('SELECT 1'))
        return {'status': 'ok', 'storage': 'postgresql' if db.engine.dialect.name == 'postgresql' else 'test-sqlite'}

    @app.get('/')
    def home():
        courses = db.session.scalars(published_courses()).all()
        cohorts = upcoming_cohorts()
        events = db.session.scalars(public_updates().where(PublicUpdate.kind == 'event',
            db.func.coalesce(PublicUpdate.ends_at, PublicUpdate.starts_at) >= now()).order_by(PublicUpdate.starts_at).limit(3)).all()
        announcements = db.session.scalars(public_updates().where(PublicUpdate.kind == 'announcement').order_by(PublicUpdate.publish_at.desc()).limit(3)).all()
        return render_template('home.html', courses=courses, cohorts=cohorts, events=events, announcements=announcements)

    @app.get('/courses/<slug>')
    def course_page(slug):
        course = db.session.scalar(published_courses().where(Course.slug == slug))
        if not course:
            abort(404)
        return render_template('course_page.html', course=course, cohorts=upcoming_cohorts(), preview=False)

    @app.get('/updates')
    def updates():
        items = db.session.scalars(public_updates().order_by(PublicUpdate.publish_at.desc(), PublicUpdate.id.desc())).all()
        return render_template('updates.html', items=items)

    @app.get('/updates/<slug>')
    def update_page(slug):
        item = db.session.scalar(public_updates().where(PublicUpdate.slug == slug))
        if not item:
            abort(404)
        return render_template('update_page.html', item=item, preview=False)

    @app.get('/admin/courses/<int:course_id>/preview')
    @admin_required
    def course_preview(course_id):
        return render_template('course_page.html', course=db.get_or_404(Course, course_id), cohorts=upcoming_cohorts(), preview=True)

    @app.get('/admin/updates/<int:update_id>/preview')
    @admin_required
    def update_preview(update_id):
        return render_template('update_page.html', item=db.get_or_404(PublicUpdate, update_id), preview=True)

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            try:
                name, email = field('name'), field('email', 254).lower()
                phone, country, kind = field('phone', 40), field('country', 80), field('kind', 12)
                password = request.form.get('password', '')
                if country not in COUNTRIES or kind not in ('new', 'existing') or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                    raise ValueError('Enter a valid email, country, and student type.')
                if not 10 <= len(password) <= 128 or password != request.form.get('confirm_password'):
                    raise ValueError('Use matching passwords with 10–128 characters.')
                student = Student(name=name, email=email, phone=phone, country=country, kind=kind,
                    password_hash=generate_password_hash(password, method='pbkdf2:sha256:600000'))
                db.session.add(student)
                db.session.flush()
                db.session.add(StudentIdentity(student_id=student.id))
                db.session.commit()
                session.clear()
                session['student_id'] = student.id
                session['student_auth'] = hmac.new(app.secret_key.encode(), student.password_hash.encode(), 'sha256').hexdigest()
                session.permanent = True
                flash(f'Your account is created. Your student number is {student.student_number}. Choose a course to continue.')
                return redirect(url_for('enrollments'))
            except ValueError as exc:
                flash(str(exc))
            except IntegrityError:
                db.session.rollback()
                flash('This email already has an account. Sign in or contact portal support.')
        return render_template('register.html')

    def throttled(key):
        key = hashlib.sha256(key.encode()).hexdigest()
        attempt = db.session.get(LoginAttempt, key)
        if attempt and aware(attempt.last_at) < now() - timedelta(minutes=15):
            attempt.attempts = 0
        if attempt and attempt.attempts >= 10:
            return key, True
        return key, False

    def failed_login(key):
        attempt = db.session.get(LoginAttempt, key)
        if not attempt:
            attempt = LoginAttempt(key=key, attempts=0)
            db.session.add(attempt)
        attempt.attempts += 1
        attempt.last_at = now()
        db.session.commit()

    @app.route('/login', methods=['GET', 'POST'])
    @app.route('/student/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()[:254]
            key, blocked = throttled('student:' + email)
            if blocked:
                flash('Too many sign-in attempts. Try again in 15 minutes or contact support.')
                return render_template('login.html', admin=False), 429
            student = db.session.scalar(select(Student).where(Student.email == email))
            password = request.form.get('password', '')
            if student and len(password) <= 128 and check_password_hash(student.password_hash, password):
                session.clear()
                session['student_id'] = student.id
                session['student_auth'] = hmac.new(app.secret_key.encode(), student.password_hash.encode(), 'sha256').hexdigest()
                session.permanent = True
                if db.session.get(LoginAttempt, key):
                    db.session.delete(db.session.get(LoginAttempt, key))
                    db.session.commit()
                return redirect(url_for('portal' if student.approved else 'enrollments'))
            failed_login(key)
            flash('Invalid email or password.')
        return render_template('login.html', admin=False)

    @app.route('/admin/login', methods=['GET', 'POST'])
    def admin_login():
        if request.method == 'POST':
            key, blocked = throttled('admin:' + (request.remote_addr or 'unknown'))
            if blocked:
                flash('Too many sign-in attempts. Try again in 15 minutes.')
                return render_template('login.html', admin=True), 429
            password = request.form.get('password', '')
            if hmac.compare_digest(request.form.get('username', '').encode(), b'admin') and hmac.compare_digest(password.encode(), app.config['ADMIN_PASSWORD'].encode()):
                session.clear()
                session['admin'] = True
                session['admin_auth'] = hmac.new(app.secret_key.encode(), app.config['ADMIN_PASSWORD'].encode(), 'sha256').hexdigest()
                session.permanent = True
                return redirect(url_for('admin'))
            failed_login(key)
            flash('Invalid admin username or password.')
        return render_template('login.html', admin=True)

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('home'))

    @app.route('/enrollments', methods=['GET', 'POST'])
    @student_required()
    def enrollments():
        cohorts = upcoming_cohorts()
        if request.method == 'POST':
            try:
                course = db.get_or_404(Course, int(field('course_id', 20)))
                if not course.publication.published:
                    raise ValueError('This course is not currently accepting new enrollments.')
                cohort_id = request.form.get('cohort_id', '')
                cohort = None
                if g.student.kind == 'new':
                    if not cohort_id:
                        raise ValueError('Choose an available start date for your course.')
                    cohort = db.get_or_404(Cohort, int(cohort_id))
                    if cohort.course_id != course.id or cohort.start_date not in next_start_dates():
                        raise ValueError('Choose a valid upcoming start date.')
                enrollment = Enrollment(student_id=g.student.id, course_id=course.id, cohort_id=cohort.id if cohort else None)
                db.session.add(enrollment)
                db.session.commit()
                flash('Enrollment requested. Pay the enrollment fee and submit your payment reference for verification.')
            except (ValueError, IntegrityError) as exc:
                db.session.rollback()
                flash(str(exc) if isinstance(exc, ValueError) else 'You have already requested this course.')
            return redirect(url_for('enrollments'))
        requested = db.session.scalars(select(Enrollment).where(Enrollment.student_id == g.student.id).order_by(Enrollment.id)).all()
        courses = db.session.scalars(published_courses()).all()
        return render_template('enrollments.html', requested=requested, courses=courses, cohorts=cohorts,
            requested_ids={e.course_id for e in requested})

    @app.post('/enrollment/<int:enrollment_id>/payment')
    @student_required()
    def payment(enrollment_id):
        enrollment = db.get_or_404(Enrollment, enrollment_id)
        if enrollment.student_id != g.student.id:
            abort(403)
        if enrollment.payment_status == 'verified':
            flash('This payment is already verified.')
            return redirect(url_for('enrollments'))
        try:
            reference = field('reference', 150)
            note = field('note', 1000, False)
            enrollment.payment_reference = reference
            enrollment.payment_note = note
            enrollment.payment_status = 'submitted'
            db.session.commit()
            flash('Payment reference submitted. Your administrator will verify it before approving portal access.')
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc) if isinstance(exc, ValueError) else 'That payment reference has already been submitted. Contact support.')
        return redirect(url_for('enrollments'))

    @app.get('/portal')
    @student_required(approved=True)
    def portal():
        enrollments = db.session.scalars(select(Enrollment).where(Enrollment.student_id == g.student.id,
            Enrollment.status == 'approved', Enrollment.payment_status == 'verified')).all()
        course_ids = [e.course_id for e in enrollments]
        classes = db.session.scalars(select(ClassSession).where(ClassSession.course_id.in_(course_ids)).order_by(ClassSession.starts_at.desc())).all()
        recordings = db.session.scalars(select(Recording).where(Recording.course_id.in_(course_ids), Recording.available_at <= now()).order_by(Recording.available_at.desc())).all()
        assessments = db.session.scalars(select(Assessment).where(Assessment.course_id.in_(course_ids)).order_by(Assessment.id.desc())).all()
        submissions = {s.assessment_id: s for s in db.session.scalars(select(Submission).where(Submission.student_id == g.student.id))}
        notices = db.session.scalars(select(Notification).where(Notification.student_id == g.student.id, Notification.available_at <= now()).order_by(Notification.id.desc()).limit(20)).all()
        registered = set(db.session.scalars(select(ClassRegistration.class_id).where(ClassRegistration.student_id == g.student.id)))
        return render_template('portal.html', enrollments=enrollments, classes=classes, recordings=recordings,
            assessments=assessments, submissions=submissions, notices=notices, registered=registered)

    @app.post('/class/<int:class_id>/register')
    @student_required(approved=True)
    def class_register(class_id):
        lesson = db.get_or_404(ClassSession, class_id)
        if not enrollment_for(lesson.course_id):
            abort(403)
        if not db.session.scalar(select(ClassRegistration).where(ClassRegistration.student_id == g.student.id, ClassRegistration.class_id == class_id)):
            db.session.add(ClassRegistration(student_id=g.student.id, class_id=class_id))
            db.session.commit()
        registration_url = lesson.registration_url or lesson.course.registration_url
        flash('Class registration recorded. Complete Zoom registration using the class link if required.')
        return render_template('class_registered.html', lesson=lesson, registration_url=registration_url)

    @app.route('/attendance', methods=['GET', 'POST'])
    @student_required(approved=True)
    def attendance():
        enrollments = db.session.scalars(select(Enrollment).where(Enrollment.student_id == g.student.id,
            Enrollment.status == 'approved', Enrollment.payment_status == 'verified')).all()
        ids = [e.course_id for e in enrollments]
        classes = db.session.scalars(select(ClassSession).where(ClassSession.course_id.in_(ids), ClassSession.starts_at <= now()).order_by(ClassSession.starts_at.desc())).all()
        if request.method == 'POST':
            try:
                lesson = db.get_or_404(ClassSession, int(field('class_id', 20)))
                if not enrollment_for(lesson.course_id) or aware(lesson.starts_at) > now():
                    abort(403)
                record = db.session.scalar(select(Attendance).where(Attendance.student_id == g.student.id, Attendance.class_id == lesson.id))
                if not record:
                    record = Attendance(student_id=g.student.id, class_id=lesson.id)
                    db.session.add(record)
                    db.session.commit()
                message = f'LionTech class attendance\nStudent number: {g.student.student_number}\nName: {g.student.name}\nCourse: {lesson.course.name}\nClass: {lesson.title}\nClass date (UTC): {aware(lesson.starts_at).strftime("%Y-%m-%d %H:%M")}\nAttendance ID: {record.id}\nStatus: Present'
                return render_template('attendance_saved.html', record=record, attendance_whatsapp=whatsapp(message))
            except ValueError:
                flash('Choose a valid class.')
        history = db.session.scalars(select(Attendance).where(Attendance.student_id == g.student.id).order_by(Attendance.submitted_at.desc())).all()
        return render_template('attendance.html', classes=classes, history=history)

    @app.post('/assessment/<int:assessment_id>/submit')
    @student_required(approved=True)
    def submit(assessment_id):
        assessment = db.get_or_404(Assessment, assessment_id)
        if not enrollment_for(assessment.course_id):
            abort(403)
        try:
            answer = field('answer', 10000)
            link = valid_url(field('url', 3000, False), optional=True)
            submission = db.session.scalar(select(Submission).where(Submission.student_id == g.student.id, Submission.assessment_id == assessment_id))
            if not submission:
                submission = Submission(student_id=g.student.id, assessment_id=assessment_id)
                db.session.add(submission)
            submission.answer, submission.url = answer, link
            submission.score, submission.feedback, submission.reviewed_at = None, '', None
            submission.submitted_at = now()
            db.session.commit()
            flash('Submitted to your instructor. Your result will appear in this portal after review.')
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for('portal', _anchor='assignments'))

    @app.post('/notification/<int:notice_id>/read')
    @student_required(approved=True)
    def notice_read(notice_id):
        notice = db.get_or_404(Notification, notice_id)
        if notice.student_id != g.student.id:
            abort(403)
        notice.read_at = now()
        db.session.commit()
        return redirect(url_for('portal', _anchor='notifications'))

    @app.route('/admin', methods=['GET', 'POST'])
    @admin_required
    def admin():
        if request.method == 'POST':
            try:
                action = field('action', 30)
                if action in ('create_course', 'edit_course'):
                    name, description, skills = field('name'), field('description', 3000), field('skills', 500)
                    state = field('publication_state', 20)
                    if state not in ('draft', 'published'):
                        raise ValueError('Choose Save draft or Publish course.')
                    if action == 'create_course':
                        course = Course(slug=content_slug(field('slug', 60, False) or name), name=name,
                                        description=description, skills=skills)
                        course.profile = ProgramProfile(duration=field('duration',150,False),
                            schedule=field('schedule',500,False), curriculum=field('curriculum',10000,False) or '\n'.join(skills.split(' · ')), tuition={})
                        course.publication = CoursePublication(published=False, payment_links={})
                        db.session.add(course)
                    else:
                        course = db.get_or_404(Course, int(field('course_id',20)))
                        course.name, course.description, course.skills = name, description, skills
                    course.publication.published = state == 'published'
                    if state == 'published':
                        course.publication.published_at = now()
                elif action in ('create_update', 'edit_update'):
                    kind = field('kind',20)
                    state = field('publication_state',20)
                    if kind not in ('event', 'announcement') or state not in ('draft', 'published'):
                        raise ValueError('Choose a valid content type and publication state.')
                    title, summary, body = field('title',150), field('summary',500), field('body',10000)
                    start = utc_field('starts_at',True) if kind == 'event' else None
                    end = utc_field('ends_at',True) if kind == 'event' else None
                    if kind == 'event' and (not start or (end and end <= start)):
                        raise ValueError('Events need a start time, and the end must be after the start.')
                    link = valid_url(field('registration_url',3000,False),True)
                    if action == 'create_update':
                        item = PublicUpdate(slug=content_slug(field('slug',60,False) or title))
                        db.session.add(item)
                    else:
                        item = db.get_or_404(PublicUpdate,int(field('update_id',20)))
                    item.kind, item.title, item.summary, item.body = kind, title, summary, body
                    item.starts_at, item.ends_at = start, end
                    item.location, item.registration_url = field('location',250,False), link
                    item.published, item.publish_at, item.updated_at = state == 'published', utc_field('publish_at',True) or now(), now()
                elif action in ('verify_payment', 'approve', 'reject'):
                    enrollment = db.get_or_404(Enrollment, int(field('enrollment_id', 20)))
                    if action == 'verify_payment':
                        if enrollment.payment_status != 'submitted' or not enrollment.payment_reference:
                            raise ValueError('The student must submit a payment reference before you verify payment.')
                        enrollment.payment_status = 'verified'
                        enrollment.verified_at = now()
                    elif action == 'approve':
                        if enrollment.payment_status != 'verified':
                            raise ValueError('Verify the enrollment payment before approving access.')
                        was_approved = enrollment.status == 'approved'
                        enrollment.status, enrollment.approved_at = 'approved', now()
                        enrollment.student.approved = True
                        if not was_approved:
                            notify(enrollment.student_id, 'Your enrollment is approved', f'Welcome to {enrollment.course.name}. Your course is now available.')
                            for recording in db.session.scalars(select(Recording).where(Recording.course_id == enrollment.course_id)):
                                if not db.session.scalar(select(Notification).where(Notification.student_id == enrollment.student_id, Notification.recording_id == recording.id)):
                                    notify(enrollment.student_id, 'Class recording available', recording.title, recording.available_at, recording.id)
                    else:
                        enrollment.status = 'rejected'
                        db.session.flush()
                        enrollment.student.approved = bool(db.session.scalar(select(Enrollment.id).where(
                            Enrollment.student_id == enrollment.student_id, Enrollment.status == 'approved',
                            Enrollment.payment_status == 'verified').limit(1)))
                        notify(enrollment.student_id, 'Enrollment needs attention', 'Please contact portal support about your enrollment.')
                elif action == 'cohort':
                    course = db.get_or_404(Course, int(field('course_id', 20)))
                    start = date.fromisoformat(field('start_date', 20))
                    if start < date.today():
                        raise ValueError('Choose today or a future start date.')
                    db.session.add(Cohort(course_id=course.id, start_date=start, label=field('label', 120)))
                elif action == 'course_links':
                    course = db.get_or_404(Course, int(field('course_id', 20)))
                    course.registration_url = valid_url(field('registration_url', 3000, False), True)
                    course.resources_url = valid_url(field('resources_url', 3000, False), True)
                elif action == 'program_profile':
                    course = db.get_or_404(Course, int(field('course_id', 20)))
                    course.profile.duration = field('duration', 150, False)
                    course.profile.schedule = field('schedule', 500, False)
                    course.profile.curriculum = field('curriculum', 10000, False)
                    course.profile.tuition = {country: field('tuition_' + str(index), 1500, False)
                                             for index, country in enumerate(COUNTRIES)}
                    if any('payment_link_' + str(i) in request.form for i in range(len(COUNTRIES))):
                        course.publication.payment_links = {country: valid_url(field('payment_link_' + str(i),3000,False),True)
                                                           for i,country in enumerate(COUNTRIES)}
                elif action == 'class':
                    course = db.get_or_404(Course, int(field('course_id', 20)))
                    db.session.add(ClassSession(course_id=course.id, title=field('title', 150), starts_at=utc_field('starts_at'),
                        registration_url=valid_url(field('registration_url', 3000, False), True)))
                elif action == 'recording':
                    course = db.get_or_404(Course, int(field('course_id', 20)))
                    recording = Recording(course_id=course.id, title=field('title', 150), url=valid_url(field('url', 3000)),
                        available_at=utc_field('available_at', True) or now())
                    db.session.add(recording)
                    db.session.flush()
                    for enrollment in db.session.scalars(select(Enrollment).where(Enrollment.course_id == course.id, Enrollment.status == 'approved', Enrollment.payment_status == 'verified')):
                        notify(enrollment.student_id, 'New class recording', recording.title, recording.available_at, recording.id)
                elif action == 'assessment':
                    course = db.get_or_404(Course, int(field('course_id', 20)))
                    kind = field('kind', 12)
                    if kind not in ('assignment', 'quiz'):
                        raise ValueError('Choose assignment or quiz.')
                    db.session.add(Assessment(course_id=course.id, title=field('title', 150), kind=kind,
                        instructions=field('instructions', 10000), due_at=utc_field('due_at', True)))
                elif action == 'grade':
                    submission = db.get_or_404(Submission, int(field('submission_id', 20)))
                    score = int(field('score', 3))
                    if not 0 <= score <= 100:
                        raise ValueError('Score must be between 0 and 100.')
                    submission.score, submission.feedback, submission.reviewed_at = score, field('feedback', 3000, False), now()
                    notify(submission.student_id, 'Your result is ready', f'{submission.assessment.title}: {score}/100')
                elif action == 'reset_password':
                    student = db.get_or_404(Student, field('student_id', 36))
                    password = request.form.get('password', '')
                    if not 10 <= len(password) <= 128:
                        raise ValueError('Use 10–128 characters for the new student password.')
                    student.password_hash = generate_password_hash(password, method='pbkdf2:sha256:600000')
                else:
                    abort(400)
                db.session.commit()
                flash('Saved successfully.')
            except (ValueError, IntegrityError) as exc:
                db.session.rollback()
                flash(str(exc) if isinstance(exc, ValueError) else 'That record already exists. Check the details.')
            return redirect(url_for('admin'))
        return render_template('admin.html', next_dates=next_start_dates(), students=db.session.scalars(select(Student).order_by(Student.created_at.desc())).all(),
            public_content=db.session.scalars(select(PublicUpdate).order_by(PublicUpdate.updated_at.desc())).all(),
            enrollments=db.session.scalars(select(Enrollment).order_by(Enrollment.created_at.desc())).all(),
            courses=db.session.scalars(select(Course).order_by(Course.id)).all(),
            cohorts=db.session.scalars(select(Cohort).order_by(Cohort.start_date)).all(),
            classes=db.session.scalars(select(ClassSession).order_by(ClassSession.starts_at.desc())).all(),
            recordings=db.session.scalars(select(Recording).order_by(Recording.available_at.desc())).all(),
            assessments=db.session.scalars(select(Assessment).order_by(Assessment.id.desc())).all(),
            submissions=db.session.scalars(select(Submission).order_by(Submission.submitted_at.desc())).all(),
            attendance=db.session.scalars(select(Attendance).order_by(Attendance.submitted_at.desc())).all())

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def handled_error(error):
        return render_template('error.html', code=error.code, message=error.description), error.code

    return app


app = create_app()
