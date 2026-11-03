
import unittest
from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from app import create_app
from extensions import db
from models import (
    User,
    Course,
    Enrollment,
    AttendanceSession,
    AttendanceRecord,
    utc_now,
)


class DatabaseTests(unittest.TestCase):

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            # Cheap hashing keeps tests fast; real accounts use scrypt.
            "PASSWORD_HASH_METHOD": "pbkdf2:sha256:1000",
            # In-memory database: fast, isolated, and never touches real data.
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SECRET_KEY": "test-key",
        })

        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        self.admin = User(
            full_name="Test Admin",
            email="admin@test.com",
            role="admin",
        )
        self.admin.set_password("AdminPass123!")

        self.lecturer = User(
            full_name="Test Lecturer",
            email="lecturer@test.com",
            role="lecturer",
        )
        self.lecturer.set_password("LecturerPass123!")

        self.student = User(
            full_name="Test Student",
            email="student@test.com",
            role="student",
            student_number="TEST001",
        )
        self.student.set_password("StudentPass123!")

        db.session.add_all([
            self.admin,
            self.lecturer,
            self.student,
        ])
        db.session.commit()

        self.course = Course(
            course_code="CSC101",
            title="Introduction to Computer Science",
            lecturer_id=self.lecturer.id,
        )
        db.session.add(self.course)
        db.session.commit()

        self.session = AttendanceSession(
            course_id=self.course.id,
            created_by=self.lecturer.id,
            qr_token_hash="a" * 64,
            expires_at=utc_now() + timedelta(minutes=15),
            is_active=True,
        )
        db.session.add(self.session)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        # Release pooled connections between tests.
        db.engine.dispose()
        self.context.pop()

    def test_users_and_password_hashing(self):
        saved = User.query.filter_by(email="student@test.com").first()

        self.assertIsNotNone(saved)
        self.assertTrue(saved.check_password("StudentPass123!"))
        self.assertFalse(saved.check_password("wrong-password"))
        self.assertNotEqual(saved.password_hash, "StudentPass123!")

    def test_course_relationship(self):
        self.assertEqual(self.course.lecturer.email, "lecturer@test.com")

    def test_enrolment(self):
        enrollment = Enrollment(
            student_id=self.student.id,
            course_id=self.course.id,
        )
        db.session.add(enrollment)
        db.session.commit()

        self.assertEqual(len(self.course.enrollments), 1)
        self.assertEqual(
            self.course.enrollments[0].student.email,
            "student@test.com",
        )

    def test_duplicate_enrolment_is_rejected(self):
        db.session.add(
            Enrollment(
                student_id=self.student.id,
                course_id=self.course.id,
            )
        )
        db.session.commit()

        db.session.add(
            Enrollment(
                student_id=self.student.id,
                course_id=self.course.id,
            )
        )

        with self.assertRaises(IntegrityError):
            db.session.commit()

        db.session.rollback()

    def test_attendance_and_duplicate_prevention(self):
        record = AttendanceRecord(
            session_id=self.session.id,
            student_id=self.student.id,
        )
        db.session.add(record)
        db.session.commit()

        saved = AttendanceRecord.query.filter_by(
            session_id=self.session.id,
            student_id=self.student.id,
        ).first()

        self.assertIsNotNone(saved)
        self.assertEqual(saved.status, "present")

        duplicate = AttendanceRecord(
            session_id=self.session.id,
            student_id=self.student.id,
        )
        db.session.add(duplicate)

        with self.assertRaises(IntegrityError):
            db.session.commit()

        db.session.rollback()

    def test_session_expiry(self):
        self.assertTrue(self.session.is_valid())

        self.session.expires_at = utc_now() - timedelta(minutes=1)
        db.session.commit()

        self.assertFalse(self.session.is_valid())

        self.session.expires_at = utc_now() + timedelta(minutes=15)
        self.session.is_active = False
        db.session.commit()

        self.assertFalse(self.session.is_valid())


if __name__ == "__main__":
    unittest.main(verbosity=2)
