"""LionTech Academy portal. Durable Postgres on Vercel; explicit SQLite for tests only."""
import hashlib
import hmac
import os
import re
import secrets
import threading
import csv
import io
from decimal import Decimal
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from werkzeug.security import check_password_hash, generate_password_hash
from vercel.blob import BlobError
from legal_content import POLICY_VERSION, EFFECTIVE_DATE, TERMS, PRIVACY
from providers import ORIGIN
from file_storage import CHUNK_SIZE, FORM_LIMIT, PrivateBlobStorage, byte_range, validate_upload, validate_image
from localization import COUNTRIES, LEGACY_COUNTRIES, COUNTRY_META, residence, formatted, converted, fetch_rates
from experience import CURRENCIES, TIMEZONES, translated, money_value, installment_schedule, csv_safe

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
    approved = db.Column(db.Boolean, default=True, nullable=False)
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


class AuthPolicy(db.Model):
    __tablename__='portal_auth_policy'
    id=db.Column(db.Integer,primary_key=True,default=1)
    username_login=db.Column(db.Boolean,nullable=False,default=True)
    number_login=db.Column(db.Boolean,nullable=False,default=True)
    email_login=db.Column(db.Boolean,nullable=False,default=True)
    google_login=db.Column(db.Boolean,nullable=False,default=False)
    facebook_login=db.Column(db.Boolean,nullable=False,default=False)
    require_email=db.Column(db.Boolean,nullable=False,default=True)
    reset_email=db.Column(db.Boolean,nullable=False,default=True)
    require_mfa=db.Column(db.Boolean,nullable=False,default=True)
    grace_days=db.Column(db.Integer,nullable=False,default=7)
    revision=db.Column(db.Integer,nullable=False,default=1)


class StudentControl(db.Model):
    __tablename__='portal_student_controls'
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),primary_key=True)
    active=db.Column(db.Boolean,nullable=False,default=True)
    revision=db.Column(db.Integer,nullable=False,default=0)
    reason=db.Column(db.String(500),nullable=False,default='')
    reviewed_at=db.Column(db.DateTime(timezone=True))


class ProfilePicture(db.Model):
    __tablename__='portal_profile_pictures'
    owner=db.Column(db.String(50),primary_key=True)
    pathname=db.Column(db.Text,nullable=False)
    updated_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)


class AdminAccount(db.Model):
    __tablename__='portal_admin_account'
    id=db.Column(db.Integer,primary_key=True,default=1)
    name=db.Column(db.String(100),nullable=False,default='LionTech Administrator')
    email=db.Column(db.String(254),nullable=False)
    password_hash=db.Column(db.Text)
    security_version=db.Column(db.Integer,nullable=False,default=1)


class LifecycleEvent(db.Model):
    __tablename__='portal_lifecycle_events'
    id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
    event_key=db.Column(db.String(180),unique=True,nullable=False)
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False,index=True)
    kind=db.Column(db.String(40),nullable=False)
    channel=db.Column(db.String(15),nullable=False,default='email')
    payload=db.Column(db.JSON,nullable=False,default=dict)
    status=db.Column(db.String(30),nullable=False,default='pending')
    attempts=db.Column(db.Integer,nullable=False,default=0)
    provider_id=db.Column(db.String(200))
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    accepted_at=db.Column(db.DateTime(timezone=True))
    student=db.relationship('Student')


class PortalAccessKey(db.Model):
    __tablename__='portal_access_keys'
    id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
    label=db.Column(db.String(100),nullable=False)
    digest=db.Column(db.String(64),unique=True,nullable=False)
    admin_stamp=db.Column(db.String(64),nullable=False)
    can_write=db.Column(db.Boolean,nullable=False,default=False)
    revoked=db.Column(db.Boolean,nullable=False,default=False)
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    expires_at=db.Column(db.DateTime(timezone=True),nullable=False)
    last_used_at=db.Column(db.DateTime(timezone=True))


class PortalTranslation(db.Model):
    __tablename__='portal_translations'
    key=db.Column(db.String(64),primary_key=True)
    language=db.Column(db.String(2),nullable=False)
    source=db.Column(db.Text,nullable=False)
    translated=db.Column(db.Text,nullable=False)
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)


class TranslationUsage(db.Model):
    __tablename__='portal_translation_usage'
    id=db.Column(db.Integer,primary_key=True)
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now,index=True)
    actor=db.Column(db.String(64),nullable=False,index=True)


class PolicyAcceptance(db.Model):
    __tablename__ = 'portal_policy_acceptances'
    student_id = db.Column(db.String(36), db.ForeignKey('portal_students.id'), primary_key=True)
    version = db.Column(db.String(30), primary_key=True)
    accepted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    method = db.Column(db.String(20), nullable=False)


def require_policy_acknowledgment():
    if request.form.get('policy_acknowledged') != 'yes' or request.form.get('policy_version') != POLICY_VERSION:
        raise ValueError('Read and acknowledge the current Terms of Service and Privacy Policy before creating your account.')


def record_policy_acceptance(student, method):
    db.session.add(PolicyAcceptance(student_id=student.id, version=POLICY_VERSION, method=method))


class AuthProfile(db.Model):
    __tablename__='portal_auth_profiles'
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),primary_key=True)
    username=db.Column(db.String(40),unique=True,nullable=False)
    email_verified_at=db.Column(db.DateTime(timezone=True))
    email_grace_until=db.Column(db.DateTime(timezone=True))
    activated_at=db.Column(db.DateTime(timezone=True))
    security_version=db.Column(db.Integer,nullable=False,default=1)


class AuthSession(db.Model):
    __tablename__='portal_auth_sessions'
    digest=db.Column(db.String(64),primary_key=True)
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False,index=True)
    stamp=db.Column(db.String(64),nullable=False)
    version=db.Column(db.Integer,nullable=False)
    expires_at=db.Column(db.DateTime(timezone=True),nullable=False)
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    mfa_at=db.Column(db.DateTime(timezone=True))
    recovery_used=db.Column(db.Boolean,nullable=False,default=False)
    next_enrollment_id=db.Column(db.Integer,db.ForeignKey('portal_enrollments.id'))


class AuthChallenge(db.Model):
    __tablename__='portal_auth_challenges'
    id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),index=True)
    purpose=db.Column(db.String(30),nullable=False,index=True)
    digest=db.Column(db.String(64),unique=True,nullable=False)
    binding=db.Column(db.String(64),nullable=False,default='')
    ciphertext=db.Column(db.Text,nullable=False)
    stamp=db.Column(db.String(64),nullable=False,default='')
    expires_at=db.Column(db.DateTime(timezone=True),nullable=False)
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    consumed=db.Column(db.Boolean,nullable=False,default=False)
    attempts=db.Column(db.Integer,nullable=False,default=0)


class AuthFactor(db.Model):
    __tablename__='portal_auth_factors'
    id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False,index=True)
    method=db.Column(db.String(15),nullable=False)
    ciphertext=db.Column(db.Text,nullable=False)
    credential_id=db.Column(db.String(1500),unique=True)
    sign_count=db.Column(db.BigInteger,nullable=False,default=0)
    last_step=db.Column(db.BigInteger,nullable=False,default=-1)
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)


class AuthRecoveryCode(db.Model):
    __tablename__='portal_auth_recovery_codes'
    digest=db.Column(db.String(64),primary_key=True)
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False,index=True)
    used_at=db.Column(db.DateTime(timezone=True))


class AuthSocialIdentity(db.Model):
    __tablename__='portal_auth_social_identities'
    provider=db.Column(db.String(15),primary_key=True)
    subject=db.Column(db.String(255),primary_key=True)
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False,index=True)


class AuthMail(db.Model):
    __tablename__='portal_auth_mail'
    id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
    ciphertext=db.Column(db.Text,nullable=False)
    status=db.Column(db.String(20),nullable=False,default='pending')
    attempts=db.Column(db.Integer,nullable=False,default=0)
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)


class AssistantDraft(db.Model):
    __tablename__='portal_assistant_drafts'
    id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
    owner=db.Column(db.String(64),nullable=False)
    action=db.Column(db.String(30),nullable=False)
    target=db.Column(db.Integer,nullable=False,default=0)
    payload=db.Column(db.JSON,nullable=False)
    fingerprint=db.Column(db.String(64),nullable=False)
    status=db.Column(db.String(20),nullable=False,default='pending')
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)


class AssistantUsage(db.Model):
    __tablename__='portal_assistant_usage'
    id=db.Column(db.Integer,primary_key=True)
    principal=db.Column(db.String(64),nullable=False,index=True)
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now,index=True)
    status=db.Column(db.String(20),nullable=False,default='requested')
    input_tokens=db.Column(db.Integer,nullable=False,default=0)
    output_tokens=db.Column(db.Integer,nullable=False,default=0)


class IntegrationDeliveryControl(db.Model):
    __tablename__='portal_integration_delivery_control'
    id=db.Column(db.Integer,primary_key=True)
    automatic=db.Column(db.Boolean,default=False,nullable=False)
    worker_checked_at=db.Column(db.DateTime(timezone=True))
    last_publish_at=db.Column(db.DateTime(timezone=True))
    publish_status=db.Column(db.String(30),default='not_tested',nullable=False)


class IntegrationSetting(db.Model):
    __tablename__='portal_integration_settings'
    provider=db.Column(db.String(30),primary_key=True)
    ciphertext=db.Column(db.Text,nullable=False)
    enabled=db.Column(db.Boolean,default=True,nullable=False)
    status=db.Column(db.String(30),default='needs_test',nullable=False)
    detail=db.Column(db.String(300),default='',nullable=False)
    revision=db.Column(db.Integer,default=1,nullable=False)
    checked_at=db.Column(db.DateTime(timezone=True))
    updated_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)


class IntegrationAudit(db.Model):
    __tablename__='portal_integration_audit'
    id=db.Column(db.Integer,primary_key=True)
    provider=db.Column(db.String(30),nullable=False)
    action=db.Column(db.String(40),nullable=False)
    status=db.Column(db.String(30),nullable=False)
    created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)


class StudentConnection(db.Model):
    __tablename__='portal_student_connections'
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),primary_key=True)
    provider=db.Column(db.String(30),primary_key=True)
    ciphertext=db.Column(db.Text,nullable=False)
    status=db.Column(db.String(30),default='connected',nullable=False)
    generation=db.Column(db.String(64),nullable=False)
    updated_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)


class CalendarOAuthState(db.Model):
    __tablename__='portal_calendar_oauth_states'
    digest=db.Column(db.String(64),primary_key=True)
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False)
    provider=db.Column(db.String(30),nullable=False)
    ciphertext=db.Column(db.Text,nullable=False)
    revision=db.Column(db.Integer,nullable=False)
    expires_at=db.Column(db.DateTime(timezone=True),nullable=False)
    consumed=db.Column(db.Boolean,default=False,nullable=False)


class StudentIntegrationPreference(db.Model):
    __tablename__='portal_student_integration_preferences'
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),primary_key=True)
    reminders=db.Column(db.Boolean,default=True,nullable=False)


class CalendarEventLink(db.Model):
    __tablename__='portal_calendar_event_links'
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),primary_key=True)
    provider=db.Column(db.String(30),primary_key=True)
    class_id=db.Column(db.Integer,db.ForeignKey('portal_classes.id'),primary_key=True)
    event_id=db.Column(db.String(500),nullable=False)
    revision=db.Column(db.Integer,default=0,nullable=False)
    generation=db.Column(db.String(64),nullable=False)


class FxSnapshot(db.Model):
    __tablename__='portal_fx_snapshots'
    id=db.Column(db.Integer,primary_key=True)
    failed=db.Column(db.Boolean,default=False,nullable=False)
    rates=db.Column(db.JSON,default=dict,nullable=False)
    as_of=db.Column(db.DateTime(timezone=True))
    checked_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)


class GlobalTuition(db.Model):
    __tablename__='portal_global_tuition'
    course_id=db.Column(db.Integer,db.ForeignKey('portal_courses.id'),primary_key=True)
    currency=db.Column(db.String(3),default='USD',nullable=False)
    total=db.Column(db.Numeric(12,2),nullable=False)
    installments=db.Column(db.Integer,default=1,nullable=False)
    full_url=db.Column(db.Text,default='',nullable=False)
    installment_url=db.Column(db.Text,default='',nullable=False)


class FeeInvoice(db.Model):
    __tablename__='portal_fee_invoices'
    id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
    enrollment_id=db.Column(db.Integer,db.ForeignKey('portal_enrollments.id'),unique=True,nullable=False)
    amount_usd=db.Column(db.Numeric(12,2),default=5,nullable=False)
    status=db.Column(db.String(20),default='pending',nullable=False)
    created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)
    enrollment=db.relationship('Enrollment')
    @property
    def number(self):return 'INV-'+str(self.created_at.year)+'-'+self.id[:10].upper()


class FeeLedger(db.Model):
    __tablename__='portal_fee_ledger_links'
    entry_id=db.Column(db.Integer,db.ForeignKey('portal_payment_entries.id'),primary_key=True)
    invoice_id=db.Column(db.String(32),db.ForeignKey('portal_fee_invoices.id'),nullable=False)


class ManualFeePayment(db.Model):
    __tablename__='portal_manual_fee_payments'
    id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
    invoice_id=db.Column(db.String(32),db.ForeignKey('portal_fee_invoices.id'),nullable=False)
    reference=db.Column(db.String(150),unique=True,nullable=False)
    amount=db.Column(db.Numeric(16,3),nullable=False)
    currency=db.Column(db.String(3),nullable=False)
    method=db.Column(db.String(40),nullable=False)
    note=db.Column(db.Text,default='',nullable=False)
    receipt_path=db.Column(db.Text)
    receipt_mime=db.Column(db.String(60))
    receipt_name=db.Column(db.String(180))
    status=db.Column(db.String(20),default='submitted',nullable=False)
    created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)
    invoice=db.relationship(FeeInvoice)


class CardCheckout(db.Model):
    __tablename__='portal_card_checkouts'
    id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
    invoice_id=db.Column(db.String(32),db.ForeignKey('portal_fee_invoices.id'),unique=True,nullable=False)
    session_id=db.Column(db.String(255),unique=True)
    expected_cents=db.Column(db.Integer,nullable=False)
    currency=db.Column(db.String(3),default='USD',nullable=False)
    tender_amount=db.Column(db.Numeric(16,3),nullable=False,default=5)
    fx_rate=db.Column(db.String(60),default='1',nullable=False)
    fx_as_of=db.Column(db.DateTime(timezone=True))
    created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)
    paid=db.Column(db.Boolean,default=False,nullable=False)
    needs_review=db.Column(db.Boolean,default=False,nullable=False)
    invoice=db.relationship(FeeInvoice)


class ScheduledClass(db.Model):
    __tablename__='portal_scheduled_class_details'
    class_id=db.Column(db.Integer,db.ForeignKey('portal_classes.id'),primary_key=True)
    cohort_id=db.Column(db.Integer,db.ForeignKey('portal_cohorts.id'))
    duration=db.Column(db.Integer,default=120,nullable=False)
    timezone=db.Column(db.String(80),default='America/Toronto',nullable=False)
    instructor=db.Column(db.String(100),default='',nullable=False)
    meeting_id=db.Column(db.String(100))
    join_minutes=db.Column(db.Integer,default=15,nullable=False)
    published=db.Column(db.Boolean,default=True,nullable=False)
    revision=db.Column(db.Integer,default=0,nullable=False)
    cohort=db.relationship('Cohort')


class ClassDelivery(db.Model):
    __tablename__='portal_class_deliveries'
    id=db.Column(db.Integer,primary_key=True)
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False)
    class_id=db.Column(db.Integer,db.ForeignKey('portal_classes.id'),nullable=False)
    revision=db.Column(db.Integer,nullable=False)
    status=db.Column(db.String(20),default='pending',nullable=False)
    attempts=db.Column(db.Integer,default=0,nullable=False)
    student=db.relationship(Student)
    lesson=db.relationship('ClassSession')
    __table_args__=(UniqueConstraint('student_id','class_id','revision'),)


class StudentPreference(db.Model):
    __tablename__='portal_student_preferences'
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),primary_key=True)
    language=db.Column(db.String(2),default='en',nullable=False)
    timezone=db.Column(db.String(60),default='America/Toronto',nullable=False)


class LessonCompletion(db.Model):
    __tablename__='portal_lesson_completions'
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),primary_key=True)
    lesson_id=db.Column(db.Integer,db.ForeignKey('portal_lessons.id'),primary_key=True)
    completed_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)


class CoursePrice(db.Model):
    __tablename__='portal_course_prices'
    course_id=db.Column(db.Integer,db.ForeignKey('portal_courses.id'),primary_key=True)
    country=db.Column(db.String(80),primary_key=True)
    currency=db.Column(db.String(3),nullable=False)
    total=db.Column(db.Numeric(12,2),nullable=False)
    installments=db.Column(db.Integer,default=1,nullable=False)
    full_url=db.Column(db.Text,default='',nullable=False)
    installment_url=db.Column(db.Text,default='',nullable=False)


class BillingSelection(db.Model):
    __tablename__='portal_billing_selections'
    enrollment_id=db.Column(db.Integer,db.ForeignKey('portal_enrollments.id'),primary_key=True)
    plan=db.Column(db.String(15),nullable=False)
    currency=db.Column(db.String(3),nullable=False)
    total=db.Column(db.Numeric(12,2),nullable=False)
    installments=db.Column(db.Integer,nullable=False)
    starts_on=db.Column(db.Date,nullable=False)
    checkout_url=db.Column(db.Text,default='',nullable=False)


class PaymentEntry(db.Model):
    __tablename__='portal_payment_entries'
    id=db.Column(db.Integer,primary_key=True)
    enrollment_id=db.Column(db.Integer,db.ForeignKey('portal_enrollments.id'),nullable=False)
    reference=db.Column(db.String(150),unique=True,nullable=False)
    amount=db.Column(db.Numeric(12,2),nullable=False)
    currency=db.Column(db.String(3),nullable=False)
    received_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)
    enrollment=db.relationship('Enrollment')


class GrowthGoal(db.Model):
    __tablename__='portal_growth_goals'
    year=db.Column(db.Integer,primary_key=True)
    students=db.Column(db.Integer,nullable=False)


class Certificate(db.Model):
    __tablename__='portal_certificates'
    id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
    student_id=db.Column(db.String(36),db.ForeignKey('portal_students.id'),nullable=False)
    course_id=db.Column(db.Integer,db.ForeignKey('portal_courses.id'),nullable=False)
    issued_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)
    student=db.relationship(Student)
    course=db.relationship('Course')
    __table_args__=(UniqueConstraint('student_id','course_id'),)


class AccountRequest(db.Model):
    __tablename__ = 'portal_account_requests'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(36), db.ForeignKey('portal_students.id'), unique=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    student = db.relationship(Student)


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


class Asset(db.Model):
    __tablename__ = 'portal_assets'
    id = db.Column(db.String(32), primary_key=True, default=lambda: secrets.token_hex(16))
    title = db.Column(db.String(180), nullable=False)
    filename = db.Column(db.String(180), nullable=False)
    mime = db.Column(db.String(150), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    purpose = db.Column(db.String(15), nullable=False)
    ready = db.Column(db.Boolean, default=False, nullable=False)
    archived = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    chunks = db.relationship('AssetChunk', order_by='AssetChunk.part')


class AssetChunk(db.Model):
    __tablename__ = 'portal_asset_chunks'
    asset_id = db.Column(db.String(32), db.ForeignKey('portal_assets.id'), primary_key=True)
    part = db.Column(db.Integer, primary_key=True)
    pathname = db.Column(db.Text, nullable=False)
    size = db.Column(db.Integer, nullable=False)
    digest = db.Column(db.String(64), nullable=False)


class Lesson(db.Model):
    __tablename__ = 'portal_lessons'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('portal_courses.id'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    module = db.Column(db.String(100), default='', nullable=False)
    body = db.Column(db.Text, default='', nullable=False)
    position = db.Column(db.Integer, default=1, nullable=False)
    published = db.Column(db.Boolean, default=False, nullable=False)
    available_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    course = db.relationship(Course)
    attachments = db.relationship('LessonFile', order_by='LessonFile.asset_id')


class LessonFile(db.Model):
    __tablename__ = 'portal_lesson_files'
    lesson_id = db.Column(db.Integer, db.ForeignKey('portal_lessons.id'), primary_key=True)
    asset_id = db.Column(db.String(32), db.ForeignKey('portal_assets.id'), primary_key=True)
    asset = db.relationship(Asset)


class SiteBranding(db.Model):
    __tablename__ = 'portal_site_branding'
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(100), default='LionTech Academy', nullable=False)
    tagline = db.Column(db.String(250), default='Practical skills. Real projects. A stronger future.', nullable=False)
    primary_color = db.Column(db.String(7), default='#061d62', nullable=False)
    accent_color = db.Column(db.String(7), default='#ffea00', nullable=False)
    logo_asset_id = db.Column(db.String(32), db.ForeignKey('portal_assets.id'))
    banner_asset_id = db.Column(db.String(32), db.ForeignKey('portal_assets.id'))


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
    joining = db.relationship('ClassJoinLink', uselist=False)


class ClassJoinLink(db.Model):
    __tablename__ = 'portal_class_join_links'
    class_id = db.Column(db.Integer, db.ForeignKey('portal_classes.id'), primary_key=True)
    url = db.Column(db.Text, default='', nullable=False)


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
    if not connection.execute(select(AuthPolicy.id).where(AuthPolicy.id==1)).first():connection.execute(AuthPolicy.__table__.insert().values(id=1))
    table = Course.__table__
    if not connection.execute(select(GrowthGoal.year).where(GrowthGoal.year==2027)).first():
        connection.execute(GrowthGoal.__table__.insert().values(year=2027,students=5000))
    if not connection.execute(select(SiteBranding.id).where(SiteBranding.id==1)).first():
        connection.execute(SiteBranding.__table__.insert().values(id=1))
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
    requests = AccountRequest.__table__
    # Account activation is independent of paid enrollment in any course.
    connection.execute(Student.__table__.update().where(Student.approved.is_(False)).values(approved=True))
    # The caller holds the schema advisory lock, so parallel cold starts cannot
    # assign two numbers to an existing account. Issued numbers are never reused.
    for sid in connection.execute(select(Student.id).order_by(Student.created_at, Student.id)).scalars():
        if not connection.execute(select(identities.c.id).where(identities.c.student_id == sid)).first():
            connection.execute(identities.insert().values(student_id=sid))
        if not connection.execute(select(requests.c.id).where(requests.c.student_id == sid)).first():
            connection.execute(requests.insert().values(student_id=sid,created_at=now()))
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


def auth_account_status(student):
    policy=db.session.get(AuthPolicy,1);profile=db.session.get(AuthProfile,student.id)
    if not policy or not policy.require_email:return 'Active'
    if profile and profile.email_verified_at:return 'Email verified · active'
    return 'Email verification pending'


def account_whatsapp(student):
    return whatsapp(f'LionTech account creation notification\nStudent number: {student.student_number}\nName: {student.name}\nEmail: {student.email}\nWhatsApp: {student.phone}\nCountry: {student.country}\nStudent type: {student.kind}\nAccount: {auth_account_status(student)}\nEnrollment: Requires payment confirmation\nReview: {ORIGIN}/admin#students')


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
        FILE_STORAGE=PrivateBlobStorage(),
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
            control=db.session.get(StudentControl,student.id)
            if control and not control.active:session.clear();student=None
        if student:
            token = hmac.new(app.secret_key.encode(), student.password_hash.encode(), 'sha256').hexdigest()
            if hmac.compare_digest(token.encode(), session.get('student_auth', '').encode()):
                g.student = student
            else:
                session.clear()
        if session.get('admin'):
            admin_token = app.extensions['admin_security']['stamp']()
            if not hmac.compare_digest(admin_token.encode(), session.get('admin_auth', '').encode()):
                session.clear()
        if request.method == 'POST' and request.endpoint not in ('stripe_webhook','chatgpt_context','chatgpt_draft','chatgpt_apply'):
            if request.endpoint in ('student_profile','admin_profile'):
                request.max_content_length=2*1024*1024+16384
            if request.endpoint == 'manual_fee':
                request.max_content_length = 2*1024*1024+8192
            if request.endpoint == 'asset_chunk':
                request.max_content_length = FORM_LIMIT
            token = session.get('csrf', '')
            if not token or not hmac.compare_digest(token.encode(), (request.headers.get('X-CSRF-Token') or request.form.get('csrf', '')).encode()):
                abort(400, 'Your form expired. Reload the page and try again.')

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers.setdefault('Referrer-Policy','strict-origin-when-cross-origin')
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; form-action 'self' https://checkout.stripe.com https://accounts.google.com https://login.microsoftonline.com https://www.facebook.com; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.context_processor
    def context():
        session.setdefault('csrf', secrets.token_hex(24))
        preference=db.session.get(StudentPreference,g.student.id) if g.student else None
        language=preference.language if preference else session.get('ui_language','en')
        timezone=preference.timezone if preference else session.get('ui_timezone','Europe/Madrid' if g.student and g.student.country=='Spain' else 'America/Toronto')
        account_request_url = account_whatsapp(g.student) if g.student else ''
        return dict(csrf=session['csrf'], support_url=whatsapp('Hello LionTech Academy, I need help with my student portal.'),
                    completion_url=whatsapp('Project completed'), account_request_url=account_request_url,
                    **app.extensions.get('global_experience',{}),
                    countries=COUNTRIES, legacy_countries=LEGACY_COUNTRIES, payments=PAYMENTS, course_payment=course_payment, account_whatsapp=account_whatsapp, auth_account_status=auth_account_status,
                    branding=db.session.get(SiteBranding,1), utcnow=now(), aware=aware,
                    ui_language=language,ui_timezone=timezone,timezones=TIMEZONES,currencies=CURRENCIES,
                    t=lambda text:translated(text,language),current_path=request.full_path.rstrip('?'),
                    billing_for=billing_data,
                    offer_for=offer_for,
                    enrollment_checkout=enrollment_checkout,
                    app_shell=request.endpoint in ('admin','portal','enrollments','file_library','lesson_page','lesson_preview','attendance','account_location','global_payment','live_classes','schedule_admin','integration_hub','integration_detail','integration_activity','connected_accounts','admin_assistant','student_assistant','assistant_review','security_account','security_settings','chatgpt_settings','student_profile','admin_profile','notification_admin','account_review') and bool(g.student or session.get('admin')))

    def student_required():
        def decorate(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if not g.student:
                    return redirect(url_for('login'))
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

    def offer_for(course_id,country):
        return db.session.get(GlobalTuition,course_id) or db.session.get(CoursePrice,(course_id,country)) or db.session.get(CoursePrice,(course_id,'Other'))

    def enrollment_checkout(enrollment):
        terms=db.session.get(BillingSelection,enrollment.id)
        return ((terms.checkout_url,'Pay selected plan') if terms.checkout_url else None) if terms else course_payment(enrollment.course,enrollment.student.country)

    def billing_data(enrollment):
        terms=db.session.get(BillingSelection,enrollment.id)
        if not terms:return None
        collected=sum((entry.amount for entry in db.session.scalars(select(PaymentEntry).where(PaymentEntry.enrollment_id==enrollment.id,PaymentEntry.currency==terms.currency,~PaymentEntry.id.in_(select(FeeLedger.entry_id))))),Decimal(0))
        remaining=max(Decimal(0),terms.total-collected)
        cumulative=Decimal(0);next_payment=None
        for part in installment_schedule(terms.total,terms.installments,terms.starts_on):
            cumulative+=part['amount']
            if cumulative>collected:
                next_payment={'amount':min(part['amount'],cumulative-collected),'due':part['due']};break
        return {'terms':terms,'collected':collected,'remaining':remaining,'next':next_payment}

    def learning_progress(student_id,course_id):
        lessons=db.session.scalars(select(Lesson).where(Lesson.course_id==course_id,Lesson.published.is_(True),Lesson.available_at<=now()).order_by(Lesson.position,Lesson.id)).all()
        completed=set(db.session.scalars(select(LessonCompletion.lesson_id).where(LessonCompletion.student_id==student_id)))
        done=sum(lesson.id in completed for lesson in lessons)
        resume=next((lesson for lesson in lessons if lesson.id not in completed),lessons[-1] if lessons else None)
        return {'lessons':lessons,'completed':completed,'done':done,'total':len(lessons),'percent':round(done/len(lessons)*100) if lessons else 0,'resume':resume}

    @app.post('/preferences')
    def preferences():
        language=field('language',2);zone=field('timezone',60)
        if language not in ('en','es','fr') or zone not in TIMEZONES:abort(400)
        if g.student:
            pref=db.session.get(StudentPreference,g.student.id) or StudentPreference(student_id=g.student.id)
            pref.language,pref.timezone=language,zone;db.session.add(pref);db.session.commit()
        session['ui_language'],session['ui_timezone']=language,zone
        destination=field('next',500,False)
        if not destination.startswith('/') or destination.startswith('//') or '\\' in destination or any(ord(c)<32 for c in destination):destination=url_for('portal' if g.student else 'home')
        return redirect(destination)

    @app.post('/lessons/<int:lesson_id>/complete')
    @student_required()
    def lesson_complete(lesson_id):
        lesson=db.get_or_404(Lesson,lesson_id)
        if not lesson.published or aware(lesson.available_at)>now() or not enrollment_for(lesson.course_id):abort(403)
        if not db.session.get(LessonCompletion,(g.student.id,lesson.id)):
            db.session.add(LessonCompletion(student_id=g.student.id,lesson_id=lesson.id));db.session.commit()
        flash('Lesson marked complete. Your progress has been updated.')
        return redirect(url_for('portal',_anchor='lessons'))

    @app.get('/apply')
    def application():
        return render_template('application.html',courses=db.session.scalars(published_courses()).all(),cohorts=upcoming_cohorts(),step=1,selected_slug=request.args.get('program',''))

    @app.post('/apply/choose')
    def application_choose():
        try:
            course_id=int(field('course_id',20));cohort_id=int(field('cohort_id',20))
        except ValueError:abort(400)
        course=db.get_or_404(Course,course_id)
        cohort=db.get_or_404(Cohort,cohort_id)
        if not course.publication.published or cohort.course_id!=course.id or cohort.start_date not in next_start_dates():abort(400)
        if g.student:
            enrollment=db.session.scalar(select(Enrollment).where(Enrollment.student_id==g.student.id,Enrollment.course_id==course.id))
            if not enrollment:
                enrollment=Enrollment(student_id=g.student.id,course_id=course.id,cohort_id=cohort.id if g.student.kind=='new' else None)
                db.session.add(enrollment);db.session.commit()
            return redirect(url_for('global_payment',enrollment_id=enrollment.id))
        session['application']={'course_id':course.id,'cohort_id':cohort.id}
        return redirect(url_for('register'))

    @app.get('/apply/payment/<int:enrollment_id>')
    @student_required()
    def application_payment(enrollment_id):
        enrollment=db.get_or_404(Enrollment,enrollment_id)
        if enrollment.student_id!=g.student.id:abort(403)
        return render_template('application_payment.html',e=enrollment,step=3,offer=offer_for(enrollment.course_id,g.student.country),billing=billing_data(enrollment))

    @app.post('/enrollment/<int:enrollment_id>/plan')
    @student_required()
    def enrollment_plan(enrollment_id):
        enrollment=db.get_or_404(Enrollment,enrollment_id)
        if enrollment.student_id!=g.student.id:abort(403)
        try:
            offer=offer_for(enrollment.course_id,g.student.country);plan=field('plan',15)
            if not offer or plan not in ('full','installments') or (plan=='installments' and offer.installments<2):raise ValueError('This payment plan is not configured for your program and country.')
            if enrollment.payment_status=='verified' or db.session.scalar(select(PaymentEntry.id).where(PaymentEntry.enrollment_id==enrollment.id).limit(1)):raise ValueError('Contact support to change a plan after payment.')
            terms=db.session.get(BillingSelection,enrollment.id) or BillingSelection(enrollment_id=enrollment.id)
            terms.plan,terms.currency,terms.total=plan,offer.currency,offer.total
            terms.installments=offer.installments if plan=='installments' else 1
            terms.checkout_url=offer.installment_url if plan=='installments' else offer.full_url
            terms.starts_on=enrollment.cohort.start_date if enrollment.cohort else academy_today()
            db.session.add(terms);db.session.commit();flash('Payment plan saved. Complete payment using the program checkout below.')
        except ValueError as error:db.session.rollback();flash(str(error))
        return redirect(url_for('application_payment',enrollment_id=enrollment.id))

    @app.get('/certificates/<certificate_id>')
    def certificate_page(certificate_id):
        certificate=db.get_or_404(Certificate,certificate_id)
        if not session.get('admin') and (not g.student or certificate.student_id!=g.student.id):abort(403)
        return render_template('certificate.html',certificate=certificate)

    def admin_overview():
        program=request.args.get('program','');country=request.args.get('country','');year=request.args.get('year','');currency=request.args.get('currency','USD')
        if (program and not program.isdigit()) or (country and country not in COUNTRIES) or (year and (not year.isdigit() or not 2000<=int(year)<=2100)) or currency not in CURRENCIES:abort(400)
        query=select(Enrollment).join(Student,Student.id==Enrollment.student_id)
        accounts=select(Student)
        if program:
            query=query.where(Enrollment.course_id==int(program))
            accounts=accounts.where(Student.id.in_(select(Enrollment.student_id).where(Enrollment.course_id==int(program))))
        if country:query=query.where(Student.country==country);accounts=accounts.where(Student.country==country)
        if year:query=query.where(db.extract('year',Enrollment.created_at)==int(year));accounts=accounts.where(db.extract('year',Student.created_at)==int(year))
        records=db.session.scalars(query.order_by(Enrollment.created_at.desc())).all()
        entries=db.session.scalars(select(PaymentEntry).where(PaymentEntry.enrollment_id.in_([e.id for e in records]),PaymentEntry.currency==currency).order_by(PaymentEntry.received_at.desc())).all()
        terms=db.session.scalars(select(BillingSelection).where(BillingSelection.enrollment_id.in_([e.id for e in records]),BillingSelection.currency==currency)).all()
        totals={}
        for entry in entries:
            if not db.session.get(FeeLedger,entry.id):totals[entry.enrollment_id]=totals.get(entry.enrollment_id,Decimal(0))+entry.amount
        collected=sum((entry.amount for entry in entries),Decimal(0))
        pending_fees=list(db.session.scalars(select(FeeInvoice).where(FeeInvoice.enrollment_id.in_([e.id for e in records]),FeeInvoice.status=='pending'))) if currency=='USD' else []
        outstanding=(sum((max(Decimal(0),item.total-totals.get(item.enrollment_id,Decimal(0))) for item in terms),Decimal(0))+sum((invoice.amount_usd for invoice in pending_fees),Decimal(0))) if terms or pending_fees else None
        confirmed=[e for e in records if e.status=='approved' and e.payment_status=='verified']
        goal=db.session.get(GrowthGoal,2027)
        page=request.args.get('page','1')
        if not page.isdigit():abort(400)
        page=max(1,int(page));start=(page-1)*50
        return {'records':records,'rows':records[start:start+50],'entries':entries,'count':len(records),'page':page,'has_next':start+50<len(records),
            'account_count':db.session.scalar(select(db.func.count()).select_from(accounts.subquery())),
            'paid_students':len({e.student_id for e in confirmed}),'pending':sum(e.payment_status!='verified' and e.status!='rejected' for e in records),
            'verified':len({e.student_id for e in records if e.payment_status=='verified'}),'active':len({e.student_id for e in confirmed}),
            'conversion':round(len(confirmed)/len(records)*100) if records else 0,'target':goal.students if goal else 5000,
            'collected':collected,'outstanding':outstanding,'forecast':collected+outstanding if outstanding is not None else None,
            'currency':currency,'program':program,'country':country,'year':year,'unrecorded':sum(e.payment_status=='verified' and e.id not in {entry.enrollment_id for entry in entries} for e in records)}

    @app.get('/admin/reports/payments.csv')
    @admin_required
    def payment_report():
        data=admin_overview();output=io.StringIO();writer=csv.writer(output)
        writer.writerow(['Student number','Student','Email','Program','Reference','Currency','Amount','Received (UTC)'])
        for entry in data['entries']:
            e=entry.enrollment
            writer.writerow([csv_safe(e.student.student_number),csv_safe(e.student.name),csv_safe(e.student.email),csv_safe(e.course.name),csv_safe(entry.reference),entry.currency,str(entry.amount),aware(entry.received_at).isoformat()])
        return Response(output.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="liontech-recorded-payments.csv"'})

    def record_payment_entry(enrollment,reference,amount,currency,received_at=None):
        amount=money_value(amount)
        if currency not in CURRENCIES:raise ValueError('Choose a supported payment currency.')
        terms=db.session.get(BillingSelection,enrollment.id)
        if terms and terms.currency!=currency:raise ValueError('Use the currency saved on this enrollment payment plan.')
        existing=db.session.scalar(select(PaymentEntry).where(PaymentEntry.reference==reference))
        if existing:
            if existing.enrollment_id!=enrollment.id or existing.amount!=amount or existing.currency!=currency:raise ValueError('That transaction reference is already recorded with different details.')
            return
        db.session.add(PaymentEntry(enrollment_id=enrollment.id,reference=reference,amount=amount,currency=currency,received_at=received_at or now()))

    def notify(student_id, title, body='', available_at=None, recording_id=None):
        db.session.add(Notification(student_id=student_id, title=title, body=body,
            available_at=available_at or now(), recording_id=recording_id))

    def confirm_enrollment(enrollment):
        if enrollment.payment_status != 'verified':
            raise ValueError('Verify the enrollment payment before approving access.')
        if enrollment.status == 'approved':
            return
        enrollment.status, enrollment.approved_at = 'approved', now()
        notify(enrollment.student_id, 'Your enrollment is approved', f'Payment confirmed. Welcome to {enrollment.course.name}. Your class access is now available.')
        for recording in db.session.scalars(select(Recording).where(Recording.course_id == enrollment.course_id)):
            if not db.session.scalar(select(Notification).where(Notification.student_id == enrollment.student_id, Notification.recording_id == recording.id)):
                notify(enrollment.student_id, 'Class recording available', recording.title, recording.available_at, recording.id)

    def asset_response(asset, download=False):
        try:
            start,end,status=byte_range(request.headers.get('Range'),asset.size)
        except ValueError:
            return Response(status=416,headers={'Content-Range':f'bytes */{asset.size}'})
        chunks=[(c.part,c.pathname,c.size,c.digest) for c in asset.chunks if c.part*CHUNK_SIZE<=end and (c.part+1)*CHUNK_SIZE>start]
        def content():
            for part,path,size,digest in chunks:
                data=app.config['FILE_STORAGE'].get(path)
                if len(data)!=size or hashlib.sha256(data).hexdigest()!=digest:
                    raise RuntimeError('Stored file integrity check failed.')
                offset=part*CHUNK_SIZE
                selected=data[max(0,start-offset):min(size,end-offset+1)]
                for index in range(0,len(selected),65536):
                    yield selected[index:index+65536]
        headers={'Content-Length':str(end-start+1),'Accept-Ranges':'bytes',
                 'Content-Disposition':f'{"attachment" if download or not asset.mime.startswith(("image/","audio/","video/")) else "inline"}; filename="{asset.filename}"'}
        if status==206: headers['Content-Range']=f'bytes {start}-{end}/{asset.size}'
        return Response(content() if request.method!='HEAD' else (),status=status,headers=headers,content_type=asset.mime)

    @app.get('/admin/library')
    @admin_required
    def file_library():
        show_archived=request.args.get('archived')=='1'
        assets=db.session.scalars(select(Asset).where(Asset.archived==show_archived).order_by(Asset.created_at.desc())).all()
        return render_template('file_library.html',assets=assets,show_archived=show_archived,chunk_size=CHUNK_SIZE)

    @app.post('/admin/assets/start')
    @admin_required
    def asset_start():
        try:
            size=int(field('size',20))
            name,mime=validate_upload(field('filename',250),size,field('purpose',15))
            asset=Asset(title=name,filename=name,mime=mime,size=size,purpose=request.form['purpose'])
            db.session.add(asset)
            db.session.commit()
            return jsonify(id=asset.id,chunk_size=CHUNK_SIZE,parts=(size+CHUNK_SIZE-1)//CHUNK_SIZE)
        except ValueError as error:
            return jsonify(error=str(error)),400

    @app.post('/admin/assets/<asset_id>/chunks/<int:part>')
    @admin_required
    def asset_chunk(asset_id,part):
        asset=db.get_or_404(Asset,asset_id)
        if asset.ready or asset.archived: return jsonify(error='This upload is closed.'),409
        parts=(asset.size+CHUNK_SIZE-1)//CHUNK_SIZE
        if part>=parts: return jsonify(error='Invalid upload part.'),400
        data=request.get_data()
        expected=min(CHUNK_SIZE,asset.size-part*CHUNK_SIZE)
        if len(data)!=expected: return jsonify(error='The upload part has the wrong size.'),400
        digest=hashlib.sha256(data).hexdigest()
        existing=db.session.get(AssetChunk,(asset.id,part))
        if existing:
            return (jsonify(saved=True),200) if existing.digest==digest else (jsonify(error='This part already contains different data. Start a new upload.'),409)
        try:
            pathname=app.config['FILE_STORAGE'].put(f'portal-files/{asset.id}/{part:04d}-{digest}.bin',data)
            db.session.add(AssetChunk(asset_id=asset.id,part=part,pathname=pathname,size=len(data),digest=digest))
            db.session.commit()
            return jsonify(saved=True)
        except BlobError:
            db.session.rollback()
            return jsonify(error='File storage is temporarily unavailable. Retry this upload.'),503
        except IntegrityError:
            db.session.rollback()
            existing=db.session.get(AssetChunk,(asset.id,part))
            return (jsonify(saved=True),200) if existing and existing.digest==digest else (jsonify(error='Upload conflict. Start a new upload.'),409)

    @app.post('/admin/assets/<asset_id>/finish')
    @admin_required
    def asset_finish(asset_id):
        asset=db.session.scalar(select(Asset).where(Asset.id==asset_id).with_for_update())
        if not asset or asset.archived: abort(404)
        if asset.ready: return jsonify(ready=True)
        parts=(asset.size+CHUNK_SIZE-1)//CHUNK_SIZE
        if len(asset.chunks)!=parts or sum(c.size for c in asset.chunks)!=asset.size or [c.part for c in asset.chunks]!=list(range(parts)):
            return jsonify(error='Some upload parts are missing. Resume the upload before completing it.'),400
        try:
            if asset.mime.startswith('image/'):
                validate_image(b''.join(app.config['FILE_STORAGE'].get(c.pathname) for c in asset.chunks),asset.mime)
            asset.ready=True
            db.session.commit()
            return jsonify(ready=True)
        except ValueError as error:
            db.session.rollback()
            return jsonify(error=str(error)),400
        except BlobError:
            db.session.rollback()
            return jsonify(error='File storage is temporarily unavailable.'),503

    @app.post('/admin/assets/<asset_id>/manage')
    @admin_required
    def asset_manage(asset_id):
        asset=db.get_or_404(Asset,asset_id)
        try:
            action=field('action',20)
            if action=='rename':
                asset.title=field('title',180)
            elif action in ('archive','restore'):
                branding=db.session.get(SiteBranding,1)
                if action=='archive' and asset.id in (branding.logo_asset_id,branding.banner_asset_id):
                    raise ValueError('Remove this image from branding settings before archiving it.')
                asset.archived=action=='archive'
            else:
                raise ValueError('Choose rename, archive or restore.')
            db.session.commit()
            flash('File updated.')
        except ValueError as error:
            db.session.rollback()
            flash(str(error))
        return redirect(url_for('file_library'))

    @app.route('/files/<asset_id>',methods=['GET','HEAD'])
    def asset_content(asset_id):
        asset=db.get_or_404(Asset,asset_id)
        if not asset.ready: abort(404)
        if not session.get('admin'):
            if not g.student: return redirect(url_for('login'))
            if asset.archived or asset.purpose!='lesson': abort(403)
            allowed=db.session.scalar(select(LessonFile.lesson_id).join(Lesson,Lesson.id==LessonFile.lesson_id).join(Enrollment,Enrollment.course_id==Lesson.course_id).where(
                LessonFile.asset_id==asset.id,Lesson.published.is_(True),Lesson.available_at<=now(),
                Enrollment.student_id==g.student.id,Enrollment.status=='approved',Enrollment.payment_status=='verified').limit(1))
            if not allowed: abort(403)
        return asset_response(asset,request.args.get('download')=='1')

    @app.get('/lessons/<int:lesson_id>')
    @student_required()
    def lesson_page(lesson_id):
        lesson=db.get_or_404(Lesson,lesson_id)
        if not lesson.published or aware(lesson.available_at)>now(): abort(404)
        if not enrollment_for(lesson.course_id): abort(403)
        return render_template('lesson_page.html',lesson=lesson,preview=False,completed=bool(db.session.get(LessonCompletion,(g.student.id,lesson.id))))

    @app.get('/admin/lessons/<int:lesson_id>/preview')
    @admin_required
    def lesson_preview(lesson_id):
        return render_template('lesson_page.html',lesson=db.get_or_404(Lesson,lesson_id),preview=True)

    @app.route('/brand/<slot>',methods=['GET','HEAD'])
    def brand_image(slot):
        branding=db.session.get(SiteBranding,1)
        asset_id={'logo':branding.logo_asset_id,'banner':branding.banner_asset_id}.get(slot)
        asset=db.session.get(Asset,asset_id) if asset_id else None
        if not asset or not asset.ready or asset.archived or asset.purpose!='branding' or not asset.mime.startswith('image/'):
            abort(404)
        return asset_response(asset)

    @app.get('/branding.css')
    def branding_css():
        branding=db.session.get(SiteBranding,1)
        return Response(f'header{{background:{branding.primary_color}}}.hero{{background:linear-gradient(120deg,{branding.primary_color},#1453de)}}.button.blue{{background:{branding.primary_color}}}.button.yellow,.nav-cta{{background:{branding.accent_color}}}.hero em,.auth-story em{{color:{branding.accent_color}}}',content_type='text/css')

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

    @app.get('/terms')
    def terms_of_service():
        return render_template('legal.html', title='Terms of Service', sections=TERMS, version=POLICY_VERSION, effective=EFFECTIVE_DATE)

    @app.get('/privacy')
    def privacy_policy():
        return render_template('legal.html', title='Privacy Policy', sections=PRIVACY, version=POLICY_VERSION, effective=EFFECTIVE_DATE)

    @app.context_processor
    def legal_context():
        return dict(policy_version=POLICY_VERSION)

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            app.extensions['auth_security']['registration_limit']()
            try:
                require_policy_acknowledgment()
                name, email = field('name'), field('email', 254).lower()
                phone, country, kind = field('phone', 40), field('country', 80), field('kind', 12)
                password = request.form.get('password', '')
                if country not in COUNTRIES or kind not in ('new', 'existing') or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                    raise ValueError('Enter a valid email, country, and student type.')
                if not 10 <= len(password) <= 128 or password != request.form.get('confirm_password'):
                    raise ValueError('Use matching passwords with 10–128 characters.')
                application=session.get('application');chosen_course=None;chosen_cohort=None
                if application:
                    chosen_course=db.session.get(Course,application.get('course_id'))
                    chosen_cohort=db.session.get(Cohort,application.get('cohort_id'))
                    if not chosen_course or not chosen_course.publication.published or not chosen_cohort or chosen_cohort.course_id!=chosen_course.id or chosen_cohort.start_date not in next_start_dates():
                        raise ValueError('Choose a current program and batch from the enrollment page before creating your account.')
                language=request.form.get('language')
                if language in (None,'','auto'):language=residence(country)['language']
                if language not in ('en','es','fr'):raise ValueError('Choose English, French or Spanish.')
                student = Student(name=name, email=email, phone=phone, country=country, kind=kind, approved=True,
                    password_hash=generate_password_hash(password, method='pbkdf2:sha256:600000'))
                db.session.add(student)
                db.session.flush()
                db.session.add(StudentIdentity(student_id=student.id))
                app.extensions['auth_security']['new_profile'](student,request.form.get('username',''))
                record_policy_acceptance(student, 'password')
                db.session.add(AccountRequest(student_id=student.id))
                db.session.add(StudentPreference(student_id=student.id,language=language,timezone=residence(country)['timezone']))
                enrollment=None
                if chosen_course:
                    enrollment=Enrollment(student_id=student.id,course_id=chosen_course.id,cohort_id=chosen_cohort.id if kind=='new' else None)
                    db.session.add(enrollment)
                db.session.commit()
                flash(f'Your student number is {student.student_number}. Email verification activates your account; verified payment activates enrollment.')
                return app.extensions['auth_security']['begin'](student,send_verification=True,next_enrollment=enrollment.id if enrollment else None)
            except ValueError as exc:
                db.session.rollback();flash(str(exc))
            except IntegrityError:
                db.session.rollback()
                flash('This email already has an account. Sign in or contact portal support.')
        chosen=session.get('application')
        return render_template('register.html',application_course=db.session.get(Course,chosen.get('course_id')) if chosen else None,step=2)

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
        return app.extensions['auth_security']['password_login']()

    @app.route('/admin/login', methods=['GET', 'POST'])
    def admin_login():
        if request.method == 'POST':
            key, blocked = throttled('admin:' + (request.remote_addr or 'unknown'))
            if blocked:
                flash('Too many sign-in attempts. Try again in 15 minutes.')
                return render_template('login.html', admin=True), 429
            password = request.form.get('password', '')
            if hmac.compare_digest(request.form.get('username', '').encode(), b'admin') and app.extensions['admin_security']['check'](password):
                session.clear()
                session['admin'] = True
                session['admin_auth'] = app.extensions['admin_security']['stamp']()
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
            if not secrets.compare_digest(request.form.get('student_number','').strip().upper(),g.student.student_number.upper()):
                raise ValueError('The student number does not match your signed-in account.')
            reference = field('reference', 150)
            note = field('note', 1000, False)
            enrollment.payment_reference = reference
            enrollment.payment_note = note
            enrollment.payment_status = 'submitted'
            db.session.commit()
            flash('Payment reference submitted. Your administrator will verify it before confirming enrollment and class access.')
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc) if isinstance(exc, ValueError) else 'That payment reference has already been submitted. Contact support.')
        return redirect(url_for('enrollments'))

    @app.get('/portal')
    @student_required()
    def portal():
        enrollments = db.session.scalars(select(Enrollment).where(Enrollment.student_id == g.student.id).order_by(Enrollment.id)).all()
        paid = db.session.scalars(select(Enrollment).where(Enrollment.student_id == g.student.id,
            Enrollment.status == 'approved', Enrollment.payment_status == 'verified')).all()
        course_ids = [e.course_id for e in paid]
        selected_ids = [e.course_id for e in enrollments]
        classes = db.session.scalars(select(ClassSession).where(ClassSession.course_id.in_(selected_ids)).order_by(ClassSession.starts_at.desc())).all()
        classes=[item for item in classes if app.extensions['global_experience']['class_visible'](item,g.student)]
        recordings = db.session.scalars(select(Recording).where(Recording.course_id.in_(course_ids), Recording.available_at <= now()).order_by(Recording.available_at.desc())).all()
        assessments = db.session.scalars(select(Assessment).where(Assessment.course_id.in_(course_ids)).order_by(Assessment.id.desc())).all()
        submissions = {s.assessment_id: s for s in db.session.scalars(select(Submission).where(Submission.student_id == g.student.id))}
        allowed_recordings = select(Recording.id).where(Recording.course_id.in_(course_ids))
        notices = db.session.scalars(select(Notification).where(Notification.student_id == g.student.id, Notification.available_at <= now(),
            db.or_(Notification.recording_id.is_(None), Notification.recording_id.in_(allowed_recordings))).order_by(Notification.id.desc()).limit(20)).all()
        registered = set(db.session.scalars(select(ClassRegistration.class_id).where(ClassRegistration.student_id == g.student.id)))
        announcements = db.session.scalars(public_updates().order_by(PublicUpdate.publish_at.desc()).limit(12)).all()
        lessons=db.session.scalars(select(Lesson).where(Lesson.course_id.in_(course_ids),Lesson.published.is_(True),Lesson.available_at<=now()).order_by(Lesson.course_id,Lesson.position,Lesson.id)).all()
        progress={e.course_id:(learning_progress(g.student.id,e.course_id) if e.course_id in course_ids else
            {'lessons':[],'completed':set(),'done':0,'total':0,'percent':0,'resume':None}) for e in enrollments}
        upcoming=[item for item in classes if aware(item.starts_at)>=now()]
        featured=paid[0] if paid else (enrollments[0] if enrollments else None)
        next_class=min(upcoming,key=lambda item:aware(item.starts_at)) if upcoming else None
        next_assignment=next((item for item in sorted(assessments,key=lambda item:aware(item.due_at) if item.due_at else now()+timedelta(days=3650)) if item.id not in submissions),None)
        return render_template('portal.html', enrollments=enrollments, paid_course_ids=set(course_ids), paid=paid,
            progress=progress,featured=featured,next_class=next_class,next_assignment=next_assignment,
            certificates=db.session.scalars(select(Certificate).where(Certificate.student_id==g.student.id)).all(),
            lessons=lessons,
            courses=db.session.scalars(published_courses()).all(), cohorts=upcoming_cohorts(),
            classes=classes, recordings=recordings, announcements=announcements,
            assessments=assessments, submissions=submissions, notices=notices, registered=registered)

    @app.get('/class/<int:class_id>/join')
    @student_required()
    def class_join(class_id):
        lesson = db.get_or_404(ClassSession,class_id)
        if not enrollment_for(lesson.course_id):
            abort(403)
        if not app.extensions['global_experience']['class_visible'](lesson,g.student) or not app.extensions['global_experience']['join_open'](lesson):
            abort(403,'This class is outside your cohort or its joining window.')
        if not lesson.joining or not lesson.joining.url:
            flash('Your instructor has not posted the class joining link yet.')
            return redirect(url_for('portal',_anchor='classes'))
        return redirect(lesson.joining.url)

    @app.get('/course/<int:course_id>/materials')
    @student_required()
    def course_materials(course_id):
        course = db.get_or_404(Course,course_id)
        if not enrollment_for(course_id):
            abort(403)
        if not course.resources_url:
            flash('Your instructor will post course materials here.')
            return redirect(url_for('portal',_anchor='my-courses'))
        return redirect(course.resources_url)

    @app.get('/course/<int:course_id>/register')
    @student_required()
    def course_registration(course_id):
        course = db.get_or_404(Course,course_id)
        if not enrollment_for(course_id):
            abort(403)
        if not course.registration_url:
            flash('Your instructor will post the class registration link here.')
            return redirect(url_for('portal',_anchor='classes'))
        return redirect(course.registration_url)

    @app.get('/recording/<int:recording_id>/watch')
    @student_required()
    def recording_watch(recording_id):
        recording = db.get_or_404(Recording,recording_id)
        if not enrollment_for(recording.course_id) or aware(recording.available_at)>now():
            abort(403)
        return redirect(recording.url)

    @app.post('/class/<int:class_id>/register')
    @student_required()
    def class_register(class_id):
        lesson = db.get_or_404(ClassSession, class_id)
        if not enrollment_for(lesson.course_id) or not app.extensions['global_experience']['class_visible'](lesson,g.student):
            abort(403)
        if not db.session.scalar(select(ClassRegistration).where(ClassRegistration.student_id == g.student.id, ClassRegistration.class_id == class_id)):
            db.session.add(ClassRegistration(student_id=g.student.id, class_id=class_id))
            db.session.commit()
        registration_url = lesson.registration_url or lesson.course.registration_url
        flash('Class registration recorded. Complete Zoom registration using the class link if required.')
        return render_template('class_registered.html', lesson=lesson, registration_url=registration_url)

    @app.route('/attendance', methods=['GET', 'POST'])
    @student_required()
    def attendance():
        enrollments = db.session.scalars(select(Enrollment).where(Enrollment.student_id == g.student.id,
            Enrollment.status == 'approved', Enrollment.payment_status == 'verified')).all()
        ids = [e.course_id for e in enrollments]
        classes = db.session.scalars(select(ClassSession).where(ClassSession.course_id.in_(ids), ClassSession.starts_at <= now()).order_by(ClassSession.starts_at.desc())).all()
        if request.method == 'POST':
            try:
                lesson = db.get_or_404(ClassSession, int(field('class_id', 20)))
                if not enrollment_for(lesson.course_id) or aware(lesson.starts_at) > now() or not app.extensions['global_experience']['class_visible'](lesson,g.student):
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
    @student_required()
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
    @student_required()
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
                app.extensions['assistant']['confirm_submission']('admin')
                if action=='course_price':
                    course=db.get_or_404(Course,int(field('course_id',20)));country=field('country',80);currency=field('currency',3)
                    count=int(field('installments',2))
                    if country not in COUNTRIES or currency not in CURRENCIES or not 1<=count<=24:raise ValueError('Choose a country, currency and 1–24 monthly installments.')
                    total=money_value(field('total',20))
                    if total/Decimal(count)<Decimal('.01'):raise ValueError('Installments must be at least 0.01.')
                    price=db.session.get(CoursePrice,(course.id,country)) or CoursePrice(course_id=course.id,country=country)
                    price.currency,price.total,price.installments=currency,total,count;db.session.add(price)
                    price.full_url=valid_url(field('full_url',3000,False),True)
                    price.installment_url=valid_url(field('installment_url',3000,False),True)
                elif action=='record_payment':
                    enrollment=db.get_or_404(Enrollment,int(field('enrollment_id',20)))
                    reference=field('reference',150)
                    record_payment_entry(enrollment,reference,field('amount',20),field('currency',3),utc_field('received_at',True))
                    enrollment.payment_reference=enrollment.payment_reference or reference
                    enrollment.payment_status,enrollment.verified_at='verified',enrollment.verified_at or now()
                elif action=='growth_goal':
                    target=int(field('students',6))
                    if not 1<=target<=999999:raise ValueError('Choose a target from 1 to 999999 students.')
                    goal=db.session.get(GrowthGoal,2027);goal.students=target
                elif action=='issue_certificate':
                    enrollment=db.get_or_404(Enrollment,int(field('enrollment_id',20)))
                    state=learning_progress(enrollment.student_id,enrollment.course_id)
                    if enrollment.status!='approved' or enrollment.payment_status!='verified' or not state['total'] or state['done']!=state['total']:raise ValueError('Certificates require confirmed paid enrollment and completion of all released lessons.')
                    if not db.session.scalar(select(Certificate.id).where(Certificate.student_id==enrollment.student_id,Certificate.course_id==enrollment.course_id)):
                        db.session.add(Certificate(student_id=enrollment.student_id,course_id=enrollment.course_id))
                        notify(enrollment.student_id,'Your certificate is ready',f'Your {enrollment.course.name} completion certificate is available in your account.')
                elif action in ('create_lesson','edit_lesson'):
                    course=db.get_or_404(Course,int(field('course_id',20)))
                    title,module,body=field('title',150),field('module',100,False),field('body',20000,False)
                    position=int(field('position',4))
                    state=field('publication_state',20)
                    if state not in ('draft','published') or not 1<=position<=9999:
                        raise ValueError('Choose a publication state and lesson position from 1 to 9999.')
                    ids=set(request.form.getlist('file_ids'))
                    if len(ids)>100: raise ValueError('Attach up to 100 files per lesson.')
                    selected=db.session.scalars(select(Asset).where(Asset.id.in_(ids),Asset.ready.is_(True),Asset.archived.is_(False),Asset.purpose=='lesson')).all()
                    if len(selected)!=len(ids): raise ValueError('Choose completed, active lesson files from the library.')
                    lesson=Lesson() if action=='create_lesson' else db.get_or_404(Lesson,int(field('lesson_id',20)))
                    lesson.course_id,lesson.title,lesson.module,lesson.body=course.id,title,module,body
                    lesson.position,lesson.published,lesson.available_at=position,state=='published',utc_field('available_at',True) or now()
                    db.session.add(lesson)
                    db.session.flush()
                    db.session.query(LessonFile).filter_by(lesson_id=lesson.id).delete(synchronize_session=False)
                    for asset in selected: db.session.add(LessonFile(lesson_id=lesson.id,asset_id=asset.id))
                elif action=='branding':
                    branding=db.session.get(SiteBranding,1)
                    primary,accent=field('primary_color',7),field('accent_color',7)
                    if not all(re.fullmatch(r'#[0-9a-fA-F]{6}',color) for color in (primary,accent)):
                        raise ValueError('Choose valid six-digit hex colors.')
                    chosen=[]
                    for key in ('logo_asset_id','banner_asset_id'):
                        value=field(key,32,False)
                        asset=db.session.get(Asset,value) if value else None
                        if value and (not asset or not asset.ready or asset.archived or asset.purpose!='branding'):
                            raise ValueError('Choose a completed branding image from the library.')
                        chosen.append(value or None)
                    branding.site_name,branding.tagline=field('site_name',100),field('tagline',250,False)
                    branding.primary_color,branding.accent_color=primary,accent
                    branding.logo_asset_id,branding.banner_asset_id=chosen
                elif action in ('create_course', 'edit_course'):
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
                elif action in ('verify_payment', 'verify_and_approve', 'approve', 'reject'):
                    enrollment = db.get_or_404(Enrollment, int(field('enrollment_id', 20)))
                    if action in ('verify_payment', 'verify_and_approve'):
                        if enrollment.payment_status not in ('submitted','verified') or not enrollment.payment_reference:
                            raise ValueError('The student must submit a payment reference before you verify payment.')
                        enrollment.payment_status = 'verified'
                        enrollment.verified_at = enrollment.verified_at or now()
                        if request.form.get('amount','').strip():
                            record_payment_entry(enrollment,enrollment.payment_reference,field('amount',20),field('currency',3))
                        if action == 'verify_and_approve':
                            confirm_enrollment(enrollment)
                    elif action == 'approve':
                        confirm_enrollment(enrollment)
                    else:
                        enrollment.status = 'rejected'
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
                    if not request.form.get('ai_draft_id'):
                        course.profile.tuition = {country: field('tuition_' + str(index), 1500, False)
                                                 for index, country in enumerate(LEGACY_COUNTRIES)}
                    if any('payment_link_' + str(i) in request.form for i in range(len(LEGACY_COUNTRIES))):
                        course.publication.payment_links = {country: valid_url(field('payment_link_' + str(i),3000,False),True)
                                                           for i,country in enumerate(LEGACY_COUNTRIES)}
                elif action == 'class':
                    course = db.get_or_404(Course, int(field('course_id', 20)))
                    lesson = ClassSession(course_id=course.id, title=field('title', 150), starts_at=utc_field('starts_at'),
                        registration_url=valid_url(field('registration_url', 3000, False), True))
                    lesson.joining = ClassJoinLink(url=valid_url(field('join_url',3000,False),True))
                    db.session.add(lesson)
                elif action == 'class_links':
                    lesson = db.get_or_404(ClassSession,int(field('class_id',20)))
                    lesson.registration_url = valid_url(field('registration_url',3000,False),True)
                    link = valid_url(field('join_url',3000,False),True)
                    if not lesson.joining:
                        lesson.joining = ClassJoinLink(url=link)
                    else:
                        lesson.joining.url = link
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
            view=request.form.get('return_view','dashboard')
            return redirect(url_for('admin',view=view) if view in ('dashboard','students','enrollments','payments','programs','classes','materials','announcements','reports','settings') else url_for('admin'))
        view=request.args.get('view','dashboard')
        if view not in ('dashboard','students','enrollments','payments','programs','classes','materials','announcements','reports','settings'):abort(404)
        return render_template('admin.html', admin_view=view,overview=admin_overview(),prices=db.session.scalars(select(CoursePrice)).all(),
            certificates=db.session.scalars(select(Certificate)).all(),next_dates=next_start_dates(), students=db.session.scalars(select(Student).order_by(Student.created_at.desc())).all(),
            lessons=db.session.scalars(select(Lesson).order_by(Lesson.course_id,Lesson.position,Lesson.id)).all(),
            lesson_assets=db.session.scalars(select(Asset).where(Asset.ready.is_(True),Asset.archived.is_(False),Asset.purpose=='lesson').order_by(Asset.created_at.desc())).all(),
            branding_assets=db.session.scalars(select(Asset).where(Asset.ready.is_(True),Asset.archived.is_(False),Asset.purpose=='branding').order_by(Asset.created_at.desc())).all(),
            account_requests=db.session.scalars(select(AccountRequest).order_by(AccountRequest.created_at.desc())).all(),
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

    from integrations import install as install_integrations
    app.extensions['integrations']=install_integrations(app,db,globals(),dict(admin_required=admin_required,student_required=student_required))
    from global_portal import install
    app.extensions['global_experience']=install(app,db,globals(),dict(student_required=student_required,admin_required=admin_required,notify=notify,confirm_enrollment=confirm_enrollment,offer_for=offer_for))
    from portal_assistant import install as install_assistant
    app.extensions['assistant']=install_assistant(app,db,globals(),dict(admin_required=admin_required,student_required=student_required))
    from auth_security import install as install_security
    app.extensions['auth_security']=install_security(app,db,globals(),dict(admin_required=admin_required))
    from account_security import install as install_account_security
    app.extensions['admin_security']=install_account_security(app,db,globals(),dict(admin_required=admin_required,student_required=student_required))
    from lifecycle_notifications import install as install_notifications
    app.extensions['notifications']=install_notifications(app,db,globals(),dict(admin_required=admin_required,confirm_enrollment=confirm_enrollment))
    from chatgpt_bridge import install as install_bridge
    app.extensions['chatgpt_bridge']=install_bridge(app,db,globals(),dict(admin_required=admin_required))
    from portal_languages import install as install_languages
    app.extensions['portal_languages']=install_languages(app,db,globals())
    return app


app = create_app()
