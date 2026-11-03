
import unittest
from datetime import timedelta

from app import create_app
from attendance import TOKEN_SESSION_KEY, open_session
from extensions import db
from models import (
    AttendanceRecord,
    AttendanceSession,
    Course,
    Enrollment,
    User,
    utc_now,
)


class QrAttendanceTests(unittest.TestCase):

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            # Cheap hashing keeps tests fast; real accounts use scrypt.
            "PASSWORD_HASH_METHOD": "pbkdf2:sha256:1000",
            "WTF_CSRF_ENABLED": False,
            # In-memory database: fast, isolated, and never touches real data.
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SECRET_KEY": "test-secret",
        })

        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        def make_user(name, email, role, number=None):
            user = User(
                full_name=name,
                email=email,
                role=role,
                student_number=number,
            )
            user.set_password("Password123!")
            return user

        self.lecturer = make_user("Lecturer One", "lecturer1@test.com", "lecturer")
        self.other_lecturer = make_user("Lecturer Two", "lecturer2@test.com", "lecturer")
        self.student = make_user("Student One", "student1@test.com", "student", "STU001")
        self.outsider = make_user("Student Two", "student2@test.com", "student", "STU002")

        db.session.add_all([
            self.lecturer,
            self.other_lecturer,
            self.student,
            self.outsider,
        ])
        db.session.commit()

        self.course = Course(
            course_code="CSC101",
            title="Introduction to Computer Science",
            lecturer_id=self.lecturer.id,
        )
        db.session.add(self.course)
        db.session.commit()

        # Only self.student is enrolled; self.outsider is not.
        db.session.add(
            Enrollment(student_id=self.student.id, course_id=self.course.id)
        )
        db.session.commit()

        self.session, self.token = open_session(
            self.course, self.lecturer, duration_minutes=10
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        # Release pooled connections between tests.
        db.engine.dispose()
        self.context.pop()

    # Each request runs in its own app context so Flask-Login never
    # reuses a user cached by a previous request from another client.
    def request(self, client, method, url, **kwargs):
        with self.app.app_context():
            return client.open(url, method=method, **kwargs)

    def client_for(self, email):
        client = self.app.test_client()
        self.request(
            client,
            "POST",
            "/login",
            data={"email": email, "password": "Password123!"},
        )
        return client

    def record_count(self):
        return db.session.scalar(
            db.select(db.func.count(AttendanceRecord.id))
        )

    # --- Tokens ---------------------------------------------------------

    def test_only_a_hash_of_the_token_is_stored(self):
        self.assertGreaterEqual(len(self.token), 43)
        self.assertNotEqual(self.session.qr_token_hash, self.token)
        self.assertEqual(
            self.session.qr_token_hash,
            AttendanceSession.hash_token(self.token),
        )

    def test_tokens_are_unique_per_session(self):
        tokens = {self.token}

        for _ in range(20):
            _, token = open_session(self.course, self.lecturer, 10)
            tokens.add(token)

        self.assertEqual(len(tokens), 21)

    # --- Valid scans ----------------------------------------------------

    def test_get_only_shows_confirmation_and_records_nothing(self):
        client = self.client_for("student1@test.com")

        response = self.request(client, "GET", f"/attend/{self.token}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mark me present", response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.record_count(), 0)

    def test_enrolled_student_can_record_attendance(self):
        client = self.client_for("student1@test.com")

        response = self.request(client, "POST", f"/attend/{self.token}")

        self.assertEqual(response.status_code, 201)
        self.assertIn(b"Your attendance has been recorded", response.data)

        record = AttendanceRecord.query.one()
        self.assertEqual(record.student_id, self.student.id)
        self.assertEqual(record.session_id, self.session.id)
        self.assertEqual(record.status, "present")

    # --- Invalid, expired and closed tokens -----------------------------

    def test_invalid_token_is_rejected(self):
        client = self.client_for("student1@test.com")

        response = self.request(client, "POST", "/attend/not-a-real-token")

        self.assertEqual(response.status_code, 404)
        self.assertIn(b"This QR code is not valid", response.data)
        self.assertEqual(self.record_count(), 0)

    def test_expired_token_is_rejected(self):
        self.session.expires_at = utc_now() - timedelta(seconds=1)
        db.session.commit()

        client = self.client_for("student1@test.com")
        response = self.request(client, "POST", f"/attend/{self.token}")

        self.assertEqual(response.status_code, 410)
        self.assertIn(b"expired", response.data)
        self.assertEqual(self.record_count(), 0)

    def test_closed_session_is_rejected(self):
        lecturer = self.client_for("lecturer1@test.com")
        self.request(lecturer, "POST", f"/lecturer/sessions/{self.session.id}/close")

        db.session.refresh(self.session)
        self.assertFalse(self.session.is_active)
        self.assertLessEqual(self.session.expires_at, utc_now())

        student = self.client_for("student1@test.com")
        response = self.request(student, "POST", f"/attend/{self.token}")

        self.assertEqual(response.status_code, 410)
        self.assertIn(b"closed", response.data)
        self.assertEqual(self.record_count(), 0)

    def test_regenerating_invalidates_the_old_token(self):
        lecturer = self.client_for("lecturer1@test.com")
        self.request(
            lecturer, "POST", f"/lecturer/sessions/{self.session.id}/regenerate"
        )

        with lecturer.session_transaction() as cookie_session:
            new_token = cookie_session[TOKEN_SESSION_KEY][str(self.session.id)]

        self.assertNotEqual(new_token, self.token)

        student = self.client_for("student1@test.com")
        old = self.request(student, "POST", f"/attend/{self.token}")
        self.assertEqual(old.status_code, 404)

        new = self.request(student, "POST", f"/attend/{new_token}")
        self.assertEqual(new.status_code, 201)
        self.assertEqual(self.record_count(), 1)

    # --- Duplicates and enrolment ---------------------------------------

    def test_duplicate_attendance_is_rejected(self):
        client = self.client_for("student1@test.com")

        first = self.request(client, "POST", f"/attend/{self.token}")
        second = self.request(client, "POST", f"/attend/{self.token}")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertIn(b"already recorded", second.data)
        self.assertEqual(self.record_count(), 1)

    def test_database_blocks_duplicates_even_if_checks_are_bypassed(self):
        from sqlalchemy.exc import IntegrityError

        for _ in range(2):
            db.session.add(AttendanceRecord(
                session_id=self.session.id,
                student_id=self.student.id,
            ))

        with self.assertRaises(IntegrityError):
            db.session.commit()

        db.session.rollback()

    def test_student_not_enrolled_is_rejected(self):
        client = self.client_for("student2@test.com")

        response = self.request(client, "POST", f"/attend/{self.token}")

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"not enrolled", response.data)
        self.assertEqual(self.record_count(), 0)

    def test_lecturer_cannot_mark_attendance(self):
        client = self.client_for("lecturer1@test.com")

        response = self.request(client, "POST", f"/attend/{self.token}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.record_count(), 0)

    # --- Login flow for scanning ----------------------------------------

    def test_scan_before_login_returns_to_scan_page(self):
        client = self.app.test_client()

        first = self.request(client, "GET", f"/attend/{self.token}")
        self.assertEqual(first.status_code, 302)
        # The token is kept in the session, not in the login URL.
        self.assertNotIn(self.token, first.headers["Location"])

        login = self.request(
            client,
            "POST",
            "/login",
            data={"email": "student1@test.com", "password": "Password123!"},
        )

        self.assertEqual(login.status_code, 302)
        self.assertEqual(login.headers["Location"], f"/attend/{self.token}")

    def test_login_ignores_external_next_url(self):
        for target in ["https://evil.example/", "//evil.example/", "/\\evil.example"]:
            client = self.app.test_client()

            with client.session_transaction() as cookie_session:
                cookie_session["next"] = target

            login = self.request(
                client,
                "POST",
                "/login",
                data={"email": "student1@test.com", "password": "Password123!"},
            )

            self.assertEqual(login.headers["Location"], "/dashboard", target)

    # --- Lecturer session management ------------------------------------

    def test_lecturer_can_start_session_for_own_course(self):
        self.session.close()
        db.session.commit()

        client = self.client_for("lecturer1@test.com")
        response = self.request(
            client,
            "POST",
            f"/lecturer/courses/{self.course.id}/sessions/start",
            data={"duration_minutes": "15"},
        )

        self.assertEqual(response.status_code, 302)

        newest = AttendanceSession.query.order_by(
            AttendanceSession.id.desc()
        ).first()
        self.assertNotEqual(newest.id, self.session.id)
        self.assertTrue(newest.is_valid())

        length = newest.expires_at - newest.started_at
        self.assertEqual(length, timedelta(minutes=15))

        page = self.request(client, "GET", response.headers["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"data:image/svg+xml;base64", page.data)

    def test_only_one_open_session_per_course(self):
        client = self.client_for("lecturer1@test.com")

        response = self.request(
            client,
            "POST",
            f"/lecturer/courses/{self.course.id}/sessions/start",
            data={"duration_minutes": "10"},
        )

        self.assertTrue(
            response.headers["Location"].endswith(
                f"/lecturer/sessions/{self.session.id}"
            )
        )
        self.assertEqual(AttendanceSession.query.count(), 1)

    def test_invalid_duration_is_rejected(self):
        self.session.close()
        db.session.commit()

        client = self.client_for("lecturer1@test.com")
        self.request(
            client,
            "POST",
            f"/lecturer/courses/{self.course.id}/sessions/start",
            data={"duration_minutes": "99999"},
        )

        self.assertEqual(AttendanceSession.query.count(), 1)

    def test_other_lecturer_cannot_manage_session(self):
        client = self.client_for("lecturer2@test.com")
        sid = self.session.id

        self.assertEqual(
            self.request(client, "GET", f"/lecturer/sessions/{sid}").status_code, 403
        )
        self.assertEqual(
            self.request(client, "GET", f"/lecturer/sessions/{sid}/status").status_code, 403
        )
        self.assertEqual(
            self.request(client, "POST", f"/lecturer/sessions/{sid}/close").status_code, 403
        )
        self.assertEqual(
            self.request(
                client,
                "POST",
                f"/lecturer/courses/{self.course.id}/sessions/start",
                data={"duration_minutes": "10"},
            ).status_code,
            403,
        )

        db.session.refresh(self.session)
        self.assertTrue(self.session.is_active)

    def test_student_cannot_open_lecturer_session_pages(self):
        client = self.client_for("student1@test.com")

        response = self.request(client, "GET", f"/lecturer/sessions/{self.session.id}")

        self.assertEqual(response.status_code, 403)

    def test_status_feed_reports_live_count(self):
        student = self.client_for("student1@test.com")
        self.request(student, "POST", f"/attend/{self.token}")

        lecturer = self.client_for("lecturer1@test.com")
        data = self.request(
            lecturer, "GET", f"/lecturer/sessions/{self.session.id}/status"
        ).get_json()

        self.assertEqual(data["present"], 1)
        self.assertEqual(data["status"], "open")
        self.assertGreater(data["seconds_left"], 0)

    def test_logout_clears_remembered_qr_tokens(self):
        # Found in the browser walkthrough: tokens used to survive logout
        # and sit in the next user's session cookie on a shared computer.
        self.session.close()
        db.session.commit()

        lecturer = self.client_for("lecturer1@test.com")
        self.request(
            lecturer,
            "POST",
            f"/lecturer/courses/{self.course.id}/sessions/start",
            data={"duration_minutes": "10"},
        )

        with lecturer.session_transaction() as cookie_session:
            self.assertTrue(cookie_session.get(TOKEN_SESSION_KEY))

        self.request(lecturer, "POST", "/logout")

        with lecturer.session_transaction() as cookie_session:
            self.assertNotIn(TOKEN_SESSION_KEY, cookie_session)

    def test_security_headers_are_set(self):
        response = self.request(self.app.test_client(), "GET", "/login")

        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "same-origin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
