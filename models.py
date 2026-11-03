from flask import current_app, has_app_context
from flask_login import UserMixin

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


# Nigeria uses West Africa Time (UTC+1) all year, with no daylight saving,
# so a fixed offset is exact and avoids needing timezone data on Windows.
LOCAL_TIMEZONE = timezone(timedelta(hours=1), "WAT")


def utc_now():
    """Current UTC time as a naive datetime.

    All timestamps are stored in UTC without timezone info, matching the
    rows already in the database. Replaces the deprecated datetime.utcnow().
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(value):
    """Convert a stored naive-UTC datetime to local (WAT) time."""
    if value is None:
        return None

    return value.replace(tzinfo=timezone.utc).astimezone(LOCAL_TIMEZONE)


class User(UserMixin, db.Model):
    """Administrator, lecturer, or student account."""

    __tablename__ = "users"

    __table_args__ = (
        db.CheckConstraint(
            "role IN ('admin', 'lecturer', 'student')",
            name="check_valid_user_role",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    student_number = db.Column(db.String(50), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    # Courses assigned to this lecturer.
    courses_taught = db.relationship(
        "Course",
        back_populates="lecturer",
        foreign_keys="Course.lecturer_id",
    )

    # Courses this student has enrolled in.
    enrollments = db.relationship(
        "Enrollment",
        back_populates="student",
        foreign_keys="Enrollment.student_id",
    )

    # Attendance submitted by this student.
    attendance_records = db.relationship(
        "AttendanceRecord",
        back_populates="student",
        foreign_keys="AttendanceRecord.student_id",
    )

    def set_password(self, password):
        """Store a password hash, never the plain-text password.

        Werkzeug's default (scrypt) is deliberately slow to resist offline
        guessing. Tests may set PASSWORD_HASH_METHOD to a cheaper method
        purely for speed; the application itself never does.
        """
        method = None

        if has_app_context():
            method = current_app.config.get("PASSWORD_HASH_METHOD")

        if method:
            self.password_hash = generate_password_hash(password, method=method)
        else:
            self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify a password against its stored hash."""
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


class Course(db.Model):
    """A course assigned to a lecturer."""

    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    course_code = db.Column(db.String(30), unique=True, nullable=False)
    title = db.Column(db.String(150), nullable=False)
    lecturer_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    lecturer = db.relationship(
        "User",
        back_populates="courses_taught",
        foreign_keys=[lecturer_id],
    )

    enrollments = db.relationship(
        "Enrollment",
        back_populates="course",
    )

    attendance_sessions = db.relationship(
        "AttendanceSession",
        back_populates="course",
    )

    def __repr__(self):
        return f"<Course {self.course_code}>"


class Enrollment(db.Model):
    """Connects a student to a course."""

    __tablename__ = "enrollments"

    __table_args__ = (
        db.UniqueConstraint(
            "student_id",
            "course_id",
            name="unique_student_course_enrollment",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )
    course_id = db.Column(
        db.Integer,
        db.ForeignKey("courses.id"),
        nullable=False,
    )
    enrolled_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    student = db.relationship(
        "User",
        back_populates="enrollments",
        foreign_keys=[student_id],
    )

    course = db.relationship(
        "Course",
        back_populates="enrollments",
    )


class AttendanceSession(db.Model):
    """One attendance-taking event for a course."""

    __tablename__ = "attendance_sessions"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(
        db.Integer,
        db.ForeignKey("courses.id"),
        nullable=False,
    )
    created_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )

    # Store a hash of the random QR token, not the raw token.
    qr_token_hash = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
    )

    started_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    course = db.relationship(
        "Course",
        back_populates="attendance_sessions",
    )

    creator = db.relationship(
        "User",
        foreign_keys=[created_by],
    )

    attendance_records = db.relationship(
        "AttendanceRecord",
        back_populates="session",
    )

    @staticmethod
    def hash_token(token):
        """SHA-256 hex digest of a raw QR token.

        Only this digest is stored, so someone who reads the database
        cannot rebuild a working QR code. A fast hash is fine here (unlike
        passwords) because the token is 256 bits of randomness, far too
        many possibilities to guess.
        """
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue_token(self):
        """Give this session a fresh random token and return it.

        Any previous token for this session stops working immediately,
        because its hash is overwritten.
        """
        token = secrets.token_urlsafe(32)
        self.qr_token_hash = self.hash_token(token)
        return token

    def is_valid(self, now=None):
        """Check whether this session is active and has not expired."""
        if now is None:
            now = utc_now()

        return self.is_active and now < self.expires_at

    def close(self, now=None):
        """Stop accepting attendance.

        The end time is moved forward to the moment of closing, so
        `expires_at` always records when the session really ended.
        """
        if now is None:
            now = utc_now()

        self.is_active = False
        self.expires_at = min(self.expires_at, now)

    @property
    def status(self):
        if self.is_valid():
            return "open"

        return "closed" if not self.is_active else "expired"


class AttendanceRecord(db.Model):
    """A student's attendance for one session."""

    __tablename__ = "attendance_records"

    __table_args__ = (
        db.UniqueConstraint(
            "session_id",
            "student_id",
            name="unique_attendance_per_student_per_session",
        ),
        db.CheckConstraint(
            "status IN ('present')",
            name="check_valid_attendance_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer,
        db.ForeignKey("attendance_sessions.id"),
        nullable=False,
    )
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )
    marked_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    status = db.Column(db.String(20), default="present", nullable=False)

    session = db.relationship(
        "AttendanceSession",
        back_populates="attendance_records",
    )

    student = db.relationship(
        "User",
        back_populates="attendance_records",
        foreign_keys=[student_id],
    )
