"""Additive workforce tables; private HR/payroll payloads are encrypted by the service."""
import secrets


def define(db,now):
    class StaffMember(db.Model):
        __tablename__='staff_members'
        id=db.Column(db.Integer,primary_key=True)
        email=db.Column(db.String(254),unique=True,nullable=False)
        name=db.Column(db.String(100),nullable=False)
        department=db.Column(db.String(80),nullable=False)
        role=db.Column(db.String(20),nullable=False,default='staff')
        scope=db.Column(db.String(20),nullable=False,default='assigned')
        supervisor_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'))
        password_hash=db.Column(db.Text,nullable=False,default='')
        active=db.Column(db.Boolean,nullable=False,default=False)
        verified_at=db.Column(db.DateTime(timezone=True))
        policy_version=db.Column(db.String(30))
        policy_accepted_at=db.Column(db.DateTime(timezone=True))
        revision=db.Column(db.Integer,nullable=False,default=1)
        timezone=db.Column(db.String(80),nullable=False,default='America/Toronto')
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
        @property
        def staff_number(self):return 'LTA-STF-'+str(self.id).zfill(4)
    class StaffInvite(db.Model):
        __tablename__='staff_invitations'
        id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),nullable=False,index=True)
        digest=db.Column(db.String(64),unique=True,nullable=False)
        expires_at=db.Column(db.DateTime(timezone=True),nullable=False)
        used=db.Column(db.Boolean,nullable=False,default=False)
        revoked=db.Column(db.Boolean,nullable=False,default=False)
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class StaffChallenge(db.Model):
        __tablename__='staff_challenges'
        id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),nullable=False)
        purpose=db.Column(db.String(20),nullable=False)
        digest=db.Column(db.String(64),nullable=False)
        binding=db.Column(db.String(100),nullable=False)
        revision=db.Column(db.Integer,nullable=False)
        expires_at=db.Column(db.DateTime(timezone=True),nullable=False)
        attempts=db.Column(db.Integer,nullable=False,default=0)
        used=db.Column(db.Boolean,nullable=False,default=False)
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class StaffGrant(db.Model):
        __tablename__='staff_record_grants'
        reviewer_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),primary_key=True)
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),primary_key=True)
    class StaffPrivateData(db.Model):
        __tablename__='staff_private_profiles'
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),primary_key=True)
        kind=db.Column(db.String(20),primary_key=True)
        ciphertext=db.Column(db.Text,nullable=False)
        status=db.Column(db.String(20),nullable=False,default='draft')
        review_note=db.Column(db.Text,nullable=False,default='')
        updated_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class StaffDocument(db.Model):
        __tablename__='staff_documents'
        id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),nullable=False,index=True)
        kind=db.Column(db.String(20),nullable=False)
        filename=db.Column(db.String(180),nullable=False)
        mime=db.Column(db.String(100),nullable=False)
        pathname=db.Column(db.Text,nullable=False)
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class StaffTask(db.Model):
        __tablename__='staff_tasks'
        id=db.Column(db.Integer,primary_key=True)
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),nullable=False,index=True)
        title=db.Column(db.String(160),nullable=False)
        description=db.Column(db.Text,nullable=False,default='')
        due_on=db.Column(db.Date,nullable=False)
        priority=db.Column(db.String(15),nullable=False,default='normal')
        status=db.Column(db.String(20),nullable=False,default='assigned')
        result=db.Column(db.Text,nullable=False,default='')
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
        updated_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class StaffReport(db.Model):
        __tablename__='staff_daily_reports'
        id=db.Column(db.Integer,primary_key=True)
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'),nullable=False,index=True)
        report_date=db.Column(db.Date,nullable=False)
        status=db.Column(db.String(25),nullable=False,default='draft')
        draft=db.Column(db.JSON,nullable=False,default=dict)
        latest=db.Column(db.Integer,nullable=False,default=0)
        __table_args__=(db.UniqueConstraint('member_id','report_date'),)
    class StaffReportVersion(db.Model):
        __tablename__='staff_report_versions'
        id=db.Column(db.Integer,primary_key=True)
        report_id=db.Column(db.Integer,db.ForeignKey('staff_daily_reports.id'),nullable=False,index=True)
        version=db.Column(db.Integer,nullable=False)
        payload=db.Column(db.JSON,nullable=False)
        submitted_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
        __table_args__=(db.UniqueConstraint('report_id','version'),)
    class StaffReview(db.Model):
        __tablename__='staff_report_reviews'
        id=db.Column(db.Integer,primary_key=True)
        version_id=db.Column(db.Integer,db.ForeignKey('staff_report_versions.id'),nullable=False)
        reviewer=db.Column(db.String(60),nullable=False)
        decision=db.Column(db.String(25),nullable=False)
        comment=db.Column(db.Text,nullable=False)
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class StaffAudit(db.Model):
        __tablename__='staff_audit'
        id=db.Column(db.Integer,primary_key=True)
        actor=db.Column(db.String(60),nullable=False)
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'))
        action=db.Column(db.String(80),nullable=False)
        detail=db.Column(db.String(1000),nullable=False,default='')
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    class StaffMail(db.Model):
        __tablename__='staff_mail_outbox'
        id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
        event_key=db.Column(db.String(200),unique=True,nullable=False)
        member_id=db.Column(db.Integer,db.ForeignKey('staff_members.id'))
        kind=db.Column(db.String(20),nullable=False)
        ciphertext=db.Column(db.Text,nullable=False)
        status=db.Column(db.String(20),nullable=False,default='pending')
        attempts=db.Column(db.Integer,nullable=False,default=0)
        created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)
    return {c.__name__:c for c in (StaffMember,StaffInvite,StaffChallenge,StaffGrant,StaffPrivateData,StaffDocument,StaffTask,StaffReport,StaffReportVersion,StaffReview,StaffAudit,StaffMail)}
