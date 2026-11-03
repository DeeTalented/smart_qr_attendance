
import csv
import io
import unittest
from datetime import date, datetime, timedelta

from app import create_app
from attendance import open_session
from extensions import db
from models import (
    AttendanceRecord,
    AttendanceSession,
    Course,
    Enrollment,
    User,
)
from reports import build_register, csv_safe


def utc(*args):
    return datetime(*args)


class RegisterAccuracyTests(unittest.TestCase):
    """Report numbers are checked against values worked out by hand.

    Four finished sessions, all at 09:00 UTC (10:00 WAT):
        S1 2 Mar, S2 9 Mar, S3 16 Mar, S4 23 Mar 2026

    Students (A, B, C enrolled 1 Mar; D enrolled 20 Mar):
        A attends S1 S2 S3 S4 -> 4/4 = 100.0%
        B attends S1 S2       -> 2/4 =  50.0%
        C attends nothing     -> 0/4 =   0.0%
        D attends S4          -> 1/1 = 100.0%  (S1-S3 were before D enrolled)

    Course average = (100 + 50 + 0 + 100) / 4 = 62.5%
    """

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
            user = User(full_name=name, email=email, role=role, student_number=number)
            user.set_password("Password123!")
            db.session.add(user)
            return user

        self.lecturer = make_user("Lecturer One", "lecturer1@test.com", "lecturer")
        self.other_lecturer = make_user("Lecturer Two", "lecturer2@test.com", "lecturer")
        self.admin = make_user("Administrator", "admin@test.com", "admin")
        self.a = make_user("Ade Adeyemi", "a@test.com", "student", "STU001")
        self.b = make_user("Bola Bello", "b@test.com", "student", "STU002")
        self.c = make_user("Chidi Okeke", "c@test.com", "student", "STU003")
        self.d = make_user("Dayo Danjuma", "d@test.com", "student", "STU004")
        db.session.commit()

        self.course = Course(
            course_code="CSC101",
            title="Introduction to Computer Science",
            lecturer_id=self.lecturer.id,
        )
        db.session.add(self.course)
        db.session.commit()

        for student, enrolled_at in [
            (self.a, utc(2026, 3, 1)),
            (self.b, utc(2026, 3, 1)),
            (self.c, utc(2026, 3, 1)),
            (self.d, utc(2026, 3, 20)),
        ]:
            db.session.add(Enrollment(
                student_id=student.id,
                course_id=self.course.id,
                enrolled_at=enrolled_at,
            ))

        self.s1 = self.add_session(utc(2026, 3, 2, 9, 0))
        self.s2 = self.add_session(utc(2026, 3, 9, 9, 0))
        self.s3 = self.add_session(utc(2026, 3, 16, 9, 0))
        self.s4 = self.add_session(utc(2026, 3, 23, 9, 0))

        for student, sessions in [
            (self.a, [self.s1, self.s2, self.s3, self.s4]),
            (self.b, [self.s1, self.s2]),
            (self.d, [self.s4]),
        ]:
            for s in sessions:
                self.mark(student, s)

        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    # --- Helpers --------------------------------------------------------

    def add_session(self, started_at, minutes=10):
        attendance_session = AttendanceSession(
            course_id=self.course.id,
            created_by=self.lecturer.id,
            started_at=started_at,
            expires_at=started_at + timedelta(minutes=minutes),
            is_active=False,
        )
        attendance_session.issue_token()
        db.session.add(attendance_session)
        db.session.commit()
        return attendance_session

    def mark(self, student, attendance_session):
        db.session.add(AttendanceRecord(
            session_id=attendance_session.id,
            student_id=student.id,
            marked_at=attendance_session.started_at + timedelta(minutes=2),
        ))

    def percentages(self, register):
        return {row.student.student_number: row.percentage for row in register.rows}

    def request(self, client, method, url, **kwargs):
        with self.app.app_context():
            return client.open(url, method=method, **kwargs)

    def client_for(self, email):
        client = self.app.test_client()
        self.request(
            client, "POST", "/login",
            data={"email": email, "password": "Password123!"},
        )
        return client

    # --- Calculation ----------------------------------------------------

    def test_full_register_matches_hand_calculation(self):
        register = build_register(self.course)

        self.assertEqual(len(register.sessions), 4)
        self.assertEqual(
            self.percentages(register),
            {"STU001": 100.0, "STU002": 50.0, "STU003": 0.0, "STU004": 100.0},
        )

        counts = {
            row.student.student_number: (row.attended, row.eligible)
            for row in register.rows
        }
        self.assertEqual(
            counts,
            {"STU001": (4, 4), "STU002": (2, 4), "STU003": (0, 4), "STU004": (1, 1)},
        )
        self.assertEqual(register.average_percentage, 62.5)

    def test_marks_per_session(self):
        register = build_register(self.course)
        marks = {row.student.student_number: row.marks for row in register.rows}

        self.assertEqual(marks["STU002"], ["present", "present", "absent", "absent"])
        self.assertEqual(marks["STU004"], ["n/a", "n/a", "n/a", "present"])

    def test_date_filter_is_inclusive(self):
        register = build_register(
            self.course, start=date(2026, 3, 9), end=date(2026, 3, 16)
        )

        self.assertEqual(
            [s.id for s in register.sessions], [self.s2.id, self.s3.id]
        )
        self.assertEqual(
            self.percentages(register),
            {"STU001": 100.0, "STU002": 50.0, "STU003": 0.0, "STU004": None},
        )
        self.assertEqual(register.average_percentage, 50.0)

    def test_date_filter_uses_local_wat_dates(self):
        # 23:30 UTC on 30 March is 00:30 WAT on 31 March.
        late = self.add_session(utc(2026, 3, 30, 23, 30))

        on_31st = build_register(self.course, start=date(2026, 3, 31), end=date(2026, 3, 31))
        on_30th = build_register(self.course, start=date(2026, 3, 30), end=date(2026, 3, 30))

        self.assertEqual([s.id for s in on_31st.sessions], [late.id])
        self.assertEqual(on_30th.sessions, [])

    def test_open_session_is_not_counted(self):
        live, _ = open_session(self.course, self.lecturer, 10)
        self.mark(self.a, live)
        db.session.commit()

        register = build_register(self.course)

        self.assertEqual(len(register.sessions), 4)
        self.assertEqual(register.open_sessions_excluded, 1)
        self.assertEqual(self.percentages(register)["STU002"], 50.0)

    def test_student_enrolled_during_session_who_attended_is_counted(self):
        late = User(full_name="Efe Eze", email="e@test.com", role="student", student_number="STU005")
        late.set_password("Password123!")
        db.session.add(late)
        db.session.commit()

        # Enrolled 5 minutes after S3 started, then scanned S3.
        db.session.add(Enrollment(
            student_id=late.id,
            course_id=self.course.id,
            enrolled_at=utc(2026, 3, 16, 9, 5),
        ))
        self.mark(late, self.s3)
        db.session.commit()

        row = next(
            r for r in build_register(self.course).rows
            if r.student.id == late.id
        )

        self.assertEqual(row.marks, ["n/a", "n/a", "present", "absent"])
        self.assertEqual(row.percentage, 50.0)

    def test_other_courses_do_not_leak_into_register(self):
        other = Course(course_code="CSC102", title="Other", lecturer_id=self.other_lecturer.id)
        db.session.add(other)
        db.session.commit()
        db.session.add(Enrollment(student_id=self.b.id, course_id=other.id, enrolled_at=utc(2026, 3, 1)))
        other_session = AttendanceSession(
            course_id=other.id,
            created_by=self.other_lecturer.id,
            started_at=utc(2026, 3, 3, 9),
            expires_at=utc(2026, 3, 3, 9, 10),
            is_active=False,
        )
        other_session.issue_token()
        db.session.add(other_session)
        db.session.commit()
        self.mark(self.b, other_session)
        db.session.commit()

        register = build_register(self.course)

        self.assertEqual(len(register.sessions), 4)
        self.assertEqual(self.percentages(register)["STU002"], 50.0)

    def test_course_with_no_sessions(self):
        empty = Course(course_code="CSC999", title="Empty", lecturer_id=self.lecturer.id)
        db.session.add(empty)
        db.session.commit()
        db.session.add(Enrollment(student_id=self.a.id, course_id=empty.id))
        db.session.commit()

        register = build_register(empty)

        self.assertEqual(register.sessions, [])
        self.assertIsNone(register.rows[0].percentage)
        self.assertIsNone(register.average_percentage)

    # --- CSV export -----------------------------------------------------

    def read_csv(self, response):
        text = response.get_data(as_text=True)
        self.assertTrue(text.startswith("﻿"))
        return list(csv.reader(io.StringIO(text.lstrip("﻿"))))

    def test_csv_export_matches_register(self):
        client = self.client_for("lecturer1@test.com")

        response = self.request(client, "GET", f"/courses/{self.course.id}/register.csv")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertIn("CSC101_attendance_all_to_all.csv", response.headers["Content-Disposition"])

        rows = self.read_csv(response)

        self.assertEqual(
            rows[0],
            [
                "Student number", "Full name",
                # Session times are shown in WAT (UTC+1).
                "2026-03-02 10:00", "2026-03-09 10:00",
                "2026-03-16 10:00", "2026-03-23 10:00",
                "Attended", "Sessions counted", "Percentage",
            ],
        )
        self.assertEqual(
            rows[1:],
            [
                ["STU001", "Ade Adeyemi", "Present", "Present", "Present", "Present", "4", "4", "100.0"],
                ["STU002", "Bola Bello", "Present", "Present", "Absent", "Absent", "2", "4", "50.0"],
                ["STU003", "Chidi Okeke", "Absent", "Absent", "Absent", "Absent", "0", "4", "0.0"],
                ["STU004", "Dayo Danjuma", "N/A", "N/A", "N/A", "Present", "1", "1", "100.0"],
            ],
        )

    def test_csv_export_respects_date_filter(self):
        client = self.client_for("lecturer1@test.com")

        response = self.request(
            client, "GET",
            f"/courses/{self.course.id}/register.csv?start=2026-03-09&end=2026-03-16",
        )
        rows = self.read_csv(response)

        self.assertEqual(len(rows[0]), 2 + 2 + 3)
        self.assertEqual(rows[4][-3:], ["0", "0", ""])  # D: no sessions counted

    def test_csv_export_contains_no_email_addresses(self):
        client = self.client_for("lecturer1@test.com")

        response = self.request(client, "GET", f"/courses/{self.course.id}/register.csv")

        self.assertNotIn("@test.com", response.get_data(as_text=True))

    def test_csv_cells_cannot_start_formulas(self):
        self.c.full_name = '=HYPERLINK("http://evil.example","Click")'
        db.session.commit()

        client = self.client_for("lecturer1@test.com")
        rows = self.read_csv(
            self.request(client, "GET", f"/courses/{self.course.id}/register.csv")
        )

        names = [row[1] for row in rows[1:]]
        self.assertIn("'" + self.c.full_name, names)
        for value in ("+1", "-1", "@SUM(A1)", "=1"):
            self.assertTrue(csv_safe(value).startswith("'"))
        self.assertEqual(csv_safe("Bola Bello"), "Bola Bello")

    # --- Pages, filters and access control ------------------------------

    def test_lecturer_sees_register_page(self):
        client = self.client_for("lecturer1@test.com")

        response = self.request(client, "GET", f"/courses/{self.course.id}/register")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"62.5%", response.data)
        self.assertIn(b"Bola Bello", response.data)

    def test_invalid_dates_are_reported_and_ignored(self):
        client = self.client_for("lecturer1@test.com")

        bad = self.request(
            client, "GET", f"/courses/{self.course.id}/register?start=2026-13-01"
        )
        self.assertEqual(bad.status_code, 200)
        self.assertIn(b"Ignored invalid start date", bad.data)
        self.assertIn(b"62.5%", bad.data)

        reversed_range = self.request(
            client, "GET",
            f"/courses/{self.course.id}/register?start=2026-03-20&end=2026-03-01",
        )
        self.assertIn(b"start date is after the end date", reversed_range.data)

    def test_admin_can_view_any_register(self):
        client = self.client_for("admin@test.com")

        self.assertEqual(
            self.request(client, "GET", f"/courses/{self.course.id}/register").status_code, 200
        )
        self.assertEqual(
            self.request(client, "GET", f"/courses/{self.course.id}/register.csv").status_code, 200
        )

    def test_other_lecturer_cannot_view_register_or_csv(self):
        client = self.client_for("lecturer2@test.com")

        self.assertEqual(
            self.request(client, "GET", f"/courses/{self.course.id}/register").status_code, 403
        )
        self.assertEqual(
            self.request(client, "GET", f"/courses/{self.course.id}/register.csv").status_code, 403
        )

    def test_students_cannot_view_course_register(self):
        client = self.client_for("b@test.com")

        self.assertEqual(
            self.request(client, "GET", f"/courses/{self.course.id}/register.csv").status_code, 403
        )

    def test_unauthenticated_export_redirects_to_login(self):
        response = self.request(
            self.app.test_client(), "GET", f"/courses/{self.course.id}/register.csv"
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_missing_course_returns_404(self):
        client = self.client_for("admin@test.com")

        self.assertEqual(self.request(client, "GET", "/courses/9999/register").status_code, 404)

    def test_course_page_counts_only_students_enrolled_at_the_time(self):
        # Found in the browser walkthrough: the denominator used everyone
        # enrolled now. D enrolled after S1-S3, so those sessions expect 3.
        #   S4: A, D of 4 -> "2 / 4"    S3: A of 3 -> "1 / 3" (was "1 / 4")
        #   S2, S1: A, B of 3 -> "2 / 3"
        client = self.client_for("lecturer1@test.com")

        page = self.request(
            client, "GET", f"/lecturer/courses/{self.course.id}"
        ).get_data(as_text=True)

        self.assertIn("2 / 4", page)
        self.assertIn("1 / 3", page)
        self.assertIn("2 / 3", page)
        self.assertNotIn("1 / 4", page)

    def test_student_page_explains_session_in_progress(self):
        open_session(self.course, self.lecturer, 10)

        client = self.client_for("b@test.com")
        response = self.request(client, "GET", "/student/attendance")

        self.assertIn(b"A session for this course is in progress", response.data)
        self.assertIn(b"2 / 4", response.data)

    def test_student_sees_only_own_attendance(self):
        client = self.client_for("b@test.com")

        response = self.request(client, "GET", "/student/attendance")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"2 / 4", response.data)
        self.assertIn(b"50.0%", response.data)
        self.assertIn(b"below 75%", response.data)
        self.assertNotIn(b"Ade Adeyemi", response.data)
        self.assertNotIn(b"Chidi Okeke", response.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
