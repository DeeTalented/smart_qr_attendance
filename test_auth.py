
import unittest

from app import create_app
from extensions import db
from models import User


class AuthenticationTests(unittest.TestCase):

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            # Cheap hashing keeps tests fast; real accounts use scrypt.
            "PASSWORD_HASH_METHOD": "pbkdf2:sha256:1000",
            "WTF_CSRF_ENABLED": False,
            # In-memory database: fast, isolated, and never touches real data.
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SECRET_KEY": "test-only-secret",
        })

        self.context = self.app.app_context()
        self.context.push()

        db.create_all()

        self.admin = User(
            full_name="Test Administrator",
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
            student_number="STU001",
        )
        self.student.set_password("StudentPass123!")

        db.session.add_all([
            self.admin,
            self.lecturer,
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

    def sign_in(self, email, password):
        return self.client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )

    def test_valid_login(self):
        response = self.sign_in(
            "student@test.com",
            "StudentPass123!",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Student Dashboard", response.data)

    def test_invalid_password(self):
        response = self.sign_in(
            "student@test.com",
            "incorrect-password",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"Invalid email address or password.",
            response.data,
        )

    def test_unauthenticated_user_is_redirected(self):
        response = self.client.get("/admin/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_student_cannot_open_admin_dashboard(self):
        self.sign_in("student@test.com", "StudentPass123!")

        response = self.client.get("/admin/dashboard")

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Access denied", response.data)

    def test_admin_can_open_admin_dashboard(self):
        self.sign_in("admin@test.com", "AdminPass123!")

        response = self.client.get("/admin/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Administrator Dashboard", response.data)

    def test_logout(self):
        self.sign_in("student@test.com", "StudentPass123!")

        response = self.client.post(
            "/logout",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sign in with your registered account", response.data)

        protected_response = self.client.get("/student/dashboard")
        self.assertEqual(protected_response.status_code, 302)


if __name__ == "__main__":
    unittest.main(verbosity=2)
