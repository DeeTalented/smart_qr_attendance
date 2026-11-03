
import os
import unittest
from unittest import mock

from app import create_app
from extensions import db
from init_db import looks_like_production, seed_sample_data
from models import (
    AttendanceRecord,
    AttendanceSession,
    Course,
    Enrollment,
    User,
)
from reports import build_register


class SeedDataTests(unittest.TestCase):

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            # Cheap hashing keeps tests fast; real accounts use scrypt.
            "PASSWORD_HASH_METHOD": "pbkdf2:sha256:1000",
            # In-memory database: fast, isolated, and never touches real data.
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SECRET_KEY": "test-secret",
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def test_seed_creates_expected_data(self):
        created = seed_sample_data("SamplePass123")

        self.assertEqual(len(created), 11)
        self.assertEqual(Course.query.count(), 3)
        self.assertEqual(AttendanceSession.query.count(), 6 + 4 + 3)
        self.assertGreater(AttendanceRecord.query.count(), 0)

        # No session is left open, so registers count all of them.
        self.assertFalse(any(s.is_valid() for s in AttendanceSession.query))

    def test_seed_uses_only_fictitious_example_addresses(self):
        seed_sample_data("SamplePass123")

        for user in User.query:
            self.assertTrue(user.email.endswith("@example.com"), user.email)

        for student in User.query.filter_by(role="student"):
            self.assertTrue(student.student_number.startswith("DEMO"))

    def test_seed_is_safe_to_run_twice(self):
        seed_sample_data("SamplePass123")
        counts = (
            User.query.count(),
            Enrollment.query.count(),
            AttendanceSession.query.count(),
            AttendanceRecord.query.count(),
        )

        created_again = seed_sample_data("AnotherPass123")

        self.assertEqual(created_again, [])
        self.assertEqual(
            counts,
            (
                User.query.count(),
                Enrollment.query.count(),
                AttendanceSession.query.count(),
                AttendanceRecord.query.count(),
            ),
        )

    def test_existing_accounts_keep_their_passwords(self):
        admin = User(full_name="Existing", email="admin@example.com", role="admin")
        admin.set_password("Original123!")
        db.session.add(admin)
        db.session.commit()

        created = seed_sample_data("SamplePass123")

        self.assertNotIn("admin@example.com", created)
        db.session.refresh(admin)
        self.assertTrue(admin.check_password("Original123!"))
        self.assertEqual(admin.full_name, "Existing")

    def test_attendance_only_for_enrolled_students_after_enrolment(self):
        seed_sample_data("SamplePass123")

        for record in AttendanceRecord.query:
            enrollment = Enrollment.query.filter_by(
                student_id=record.student_id,
                course_id=record.session.course_id,
            ).one()
            self.assertLessEqual(enrollment.enrolled_at, record.session.started_at)

    def test_late_enrolment_appears_as_not_counted(self):
        seed_sample_data("SamplePass123")

        course = Course.query.filter_by(course_code="CSC101").one()
        row = next(
            r for r in build_register(course).rows
            if r.student.student_number == "DEMO008"
        )

        self.assertEqual(row.marks[:3], ["n/a", "n/a", "n/a"])
        self.assertEqual(row.eligible, 3)

    def test_production_guard(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": "real-key"}, clear=False):
            os.environ.pop("FLASK_DEBUG", None)
            self.assertTrue(looks_like_production())

        with mock.patch.dict(os.environ, {"SECRET_KEY": "real-key", "FLASK_DEBUG": "1"}):
            self.assertFalse(looks_like_production())

        env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(looks_like_production())


if __name__ == "__main__":
    unittest.main(verbosity=2)
