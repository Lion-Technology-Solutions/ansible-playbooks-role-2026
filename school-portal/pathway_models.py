"""Additive tables; existing courses, lessons, enrollment and submissions remain intact."""
import secrets

def define(db):
    class CybersecurityUnit(db.Model):
        __tablename__='portal_cybersecurity_units'
        course_id=db.Column(db.Integer,db.ForeignKey('portal_courses.id'),primary_key=True)
        number=db.Column(db.Integer,primary_key=True)
        lesson_id=db.Column(db.Integer,db.ForeignKey('portal_lessons.id'),unique=True,nullable=False)
        lesson=db.relationship('Lesson')
    class CloudUnit(db.Model):
        __tablename__='portal_cloud_units'
        course_id=db.Column(db.Integer,db.ForeignKey('portal_courses.id'),primary_key=True)
        number=db.Column(db.Integer,primary_key=True)
        lesson_id=db.Column(db.Integer,db.ForeignKey('portal_lessons.id'),unique=True,nullable=False)
        lesson=db.relationship('Lesson')
    class LabExercise(db.Model):
        __tablename__='portal_lab_exercises'
        id=db.Column(db.Integer,primary_key=True)
        course_id=db.Column(db.Integer,db.ForeignKey('portal_courses.id'),nullable=False)
        number=db.Column(db.Integer,nullable=False)
        assessment_id=db.Column(db.Integer,db.ForeignKey('portal_assessments.id'),unique=True,nullable=False)
        class_id=db.Column(db.Integer,db.ForeignKey('portal_classes.id'))
        cohort_id=db.Column(db.Integer,db.ForeignKey('portal_cohorts.id'))
        published=db.Column(db.Boolean,nullable=False,default=False)
        revision=db.Column(db.Integer,nullable=False,default=0)
        assessment=db.relationship('Assessment')
        scheduled_class=db.relationship('ClassSession')
        __table_args__=(db.UniqueConstraint('course_id','number'),)
    class LabEvidence(db.Model):
        __tablename__='portal_lab_evidence'
        id=db.Column(db.String(32),primary_key=True,default=lambda:secrets.token_hex(16))
        submission_id=db.Column(db.Integer,db.ForeignKey('portal_submissions.id'),nullable=False)
        filename=db.Column(db.String(180),nullable=False)
        pathname=db.Column(db.Text,nullable=False)
        mime=db.Column(db.String(100),nullable=False)
        submission=db.relationship('Submission')
    return dict(CloudUnit=CloudUnit,CybersecurityUnit=CybersecurityUnit,LabExercise=LabExercise,LabEvidence=LabEvidence)
