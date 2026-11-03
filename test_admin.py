
import unittest

from app import create_app
from extensions import db
from models import User, Course


class AdminManagementTests(unittest.TestCase):

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

        self.admin = User(
            full_name="Administrator",
            email="admin@test.com",
            role="admin",
        )
        self.admin.set_password("AdminPass123!")

        self.lecturer = User(
            full_name="Lecturer One",
            email="lecturer1@test.com",
            role="lecturer",
        )
        self.lecturer.set_password("LecturerPass123!")

        self.other_lecturer = User(
            full_name="Lecturer Two",
            email="lecturer2@test.com",
            role="lecturer",
        )
        self.other_lecturer.set_password("LecturerPass123!")

        self.student = User(
            full_name="Student One",
            email="student1@test.com",
            role="student",
            student_number="STU001",
        )
        self.student.set_password("StudentPass123!")

        db.session.add_all([
            self.admin,
            self.lecturer,
            self.other_lecturer,
            self.student,
        ])
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        # Release pooled connections between tests.
        db.engine.dispose()
        self.context.pop()

    def login(self, email, password, client=None):
        return (client or self.client).post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )

    def create_user(self, **overrides):
        data = {
            "full_name": "New Student",
            "email": "New.Student@Test.com",
            "role": "student",
            "student_number": "stu002",
            "password": "NewPass123!",
            "confirm_password": "NewPass123!",
        }
        data.update(overrides)

        return self.client.post(
            "/admin/users/new",
            data=data,
            follow_redirects=True,
        )

    # --- Access control -------------------------------------------------

    def test_unauthenticated_user_is_redirected(self):
        response = self.client.get("/admin/users")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_lecturer_cannot_manage_users(self):
        self.login("lecturer1@test.com", "LecturerPass123!")

        self.assertEqual(self.client.get("/admin/users").status_code, 403)

        response = self.create_user()
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(
            User.query.filter_by(email="new.student@test.com").first()
        )

    def test_student_cannot_reset_passwords(self):
        self.login("student1@test.com", "StudentPass123!")

        response = self.client.post(
            f"/admin/users/{self.admin.id}/password",
            data={"password": "Hacked123!", "confirm_password": "Hacked123!"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.admin.check_password("AdminPass123!"))

    # --- Creating and editing users -------------------------------------

    def test_admin_can_create_student_who_can_then_log_in(self):
        self.login("admin@test.com", "AdminPass123!")

        response = self.create_user()
        self.assertEqual(response.status_code, 200)

        created = User.query.filter_by(email="new.student@test.com").first()
        self.assertIsNotNone(created)
        self.assertEqual(created.role, "student")
        self.assertEqual(created.student_number, "STU002")
        self.assertNotEqual(created.password_hash, "NewPass123!")

        # A fresh app context gives the new client its own `g`, so
        # Flask-Login does not reuse the admin cached by earlier requests.
        with self.app.app_context():
            other_client = self.app.test_client()
            login_response = self.login(
                "new.student@test.com", "NewPass123!", client=other_client
            )
        self.assertIn(b"Student Dashboard", login_response.data)

    def test_student_requires_student_number(self):
        self.login("admin@test.com", "AdminPass123!")

        response = self.create_user(student_number="")

        self.assertIn(b"A student number is required", response.data)
        self.assertIsNone(
            User.query.filter_by(email="new.student@test.com").first()
        )

    def test_duplicate_email_is_rejected_case_insensitively(self):
        self.login("admin@test.com", "AdminPass123!")

        response = self.create_user(email="STUDENT1@test.com")

        self.assertIn(b"Another account already uses this email.", response.data)
        self.assertEqual(User.query.count(), 4)

    def test_duplicate_student_number_is_rejected(self):
        self.login("admin@test.com", "AdminPass123!")

        response = self.create_user(student_number="stu001")

        self.assertIn(b"Another student already has this number.", response.data)
        self.assertEqual(User.query.count(), 4)

    def test_password_confirmation_and_length_are_enforced(self):
        self.login("admin@test.com", "AdminPass123!")

        self.create_user(password="short", confirm_password="short")
        self.create_user(confirm_password="Different123!")

        self.assertEqual(User.query.count(), 4)

    def test_staff_accounts_never_store_a_student_number(self):
        self.login("admin@test.com", "AdminPass123!")

        self.create_user(
            email="new.lecturer@test.com",
            role="lecturer",
            student_number="SHOULD-BE-IGNORED",
        )

        created = User.query.filter_by(email="new.lecturer@test.com").first()
        self.assertIsNotNone(created)
        self.assertIsNone(created.student_number)

    def test_admin_can_edit_user_details(self):
        self.login("admin@test.com", "AdminPass123!")

        self.client.post(
            f"/admin/users/{self.student.id}/edit",
            data={
                "full_name": "Student Renamed",
                "email": "renamed@test.com",
                "student_number": "STU001",
            },
            follow_redirects=True,
        )

        db.session.refresh(self.student)
        self.assertEqual(self.student.full_name, "Student Renamed")
        self.assertEqual(self.student.email, "renamed@test.com")
        self.assertEqual(self.student.role, "student")

    def test_admin_password_reset_replaces_old_password(self):
        self.login("admin@test.com", "AdminPass123!")

        self.client.post(
            f"/admin/users/{self.student.id}/password",
            data={
                "password": "BrandNew123!",
                "confirm_password": "BrandNew123!",
            },
            follow_redirects=True,
        )

        db.session.refresh(self.student)
        self.assertTrue(self.student.check_password("BrandNew123!"))
        self.assertFalse(self.student.check_password("StudentPass123!"))

    def test_editing_missing_user_returns_404(self):
        self.login("admin@test.com", "AdminPass123!")

        self.assertEqual(self.client.get("/admin/users/9999/edit").status_code, 404)

    # --- Courses --------------------------------------------------------

    def test_admin_can_create_course_for_a_lecturer(self):
        self.login("admin@test.com", "AdminPass123!")

        self.client.post(
            "/admin/courses/new",
            data={
                "course_code": "csc301",
                "title": "Operating Systems",
                "lecturer_id": str(self.lecturer.id),
            },
            follow_redirects=True,
        )

        course = Course.query.filter_by(course_code="CSC301").first()
        self.assertIsNotNone(course)
        self.assertEqual(course.lecturer_id, self.lecturer.id)

    def test_admin_cannot_assign_course_to_a_student(self):
        self.login("admin@test.com", "AdminPass123!")

        self.client.post(
            "/admin/courses/new",
            data={
                "course_code": "CSC302",
                "title": "Databases",
                "lecturer_id": str(self.student.id),
            },
            follow_redirects=True,
        )

        self.assertIsNone(Course.query.filter_by(course_code="CSC302").first())

    def test_admin_can_reassign_course_lecturer(self):
        course = Course(
            course_code="CSC101",
            title="Introduction to Computer Science",
            lecturer_id=self.lecturer.id,
        )
        db.session.add(course)
        db.session.commit()

        self.login("admin@test.com", "AdminPass123!")

        self.client.post(
            f"/admin/courses/{course.id}/edit",
            data={
                "course_code": "CSC101",
                "title": "Introduction to Computer Science",
                "lecturer_id": str(self.other_lecturer.id),
            },
            follow_redirects=True,
        )

        db.session.refresh(course)
        self.assertEqual(course.lecturer_id, self.other_lecturer.id)

    def test_lecturer_cannot_use_admin_course_routes(self):
        self.login("lecturer1@test.com", "LecturerPass123!")

        response = self.client.get("/admin/courses/new")

        self.assertEqual(response.status_code, 403)

    # --- Self-service password change -----------------------------------

    def test_change_password_requires_current_password(self):
        self.login("student1@test.com", "StudentPass123!")

        response = self.client.post(
            "/account/password",
            data={
                "current_password": "wrong-password",
                "password": "Changed123!",
                "confirm_password": "Changed123!",
            },
            follow_redirects=True,
        )

        self.assertIn(b"Your current password is incorrect.", response.data)
        db.session.refresh(self.student)
        self.assertTrue(self.student.check_password("StudentPass123!"))

    def test_user_can_change_own_password(self):
        self.login("student1@test.com", "StudentPass123!")

        self.client.post(
            "/account/password",
            data={
                "current_password": "StudentPass123!",
                "password": "Changed123!",
                "confirm_password": "Changed123!",
            },
            follow_redirects=True,
        )

        db.session.refresh(self.student)
        self.assertTrue(self.student.check_password("Changed123!"))


class AdminCsrfTests(unittest.TestCase):
    """CSRF protection stays on here, as it is in the real application."""

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

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def test_post_without_csrf_token_is_rejected(self):
        response = self.client.post(
            "/login",
            data={"email": "admin@test.com", "password": "anything"},
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
