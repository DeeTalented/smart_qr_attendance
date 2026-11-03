
import unittest

from app import create_app
from extensions import db
from models import User, Course, Enrollment


class CourseManagementTests(unittest.TestCase):

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

        self.admin = User(
            full_name="Administrator",
            email="admin@test.com",
            role="admin",
        )
        self.admin.set_password("AdminPass123!")

        db.session.add_all([
            self.lecturer,
            self.other_lecturer,
            self.student,
            self.admin,
        ])
        db.session.commit()

        self.course = Course(
            course_code="CSC101",
            title="Introduction to Computer Science",
            lecturer_id=self.lecturer.id,
        )

        self.other_course = Course(
            course_code="CSC102",
            title="Programming Fundamentals",
            lecturer_id=self.other_lecturer.id,
        )

        db.session.add_all([self.course, self.other_course])
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        # Release pooled connections between tests.
        db.engine.dispose()
        self.context.pop()

    def login(self, email, password):
        return self.client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )

    def test_lecturer_sees_own_courses(self):
        self.login("lecturer1@test.com", "LecturerPass123!")

        response = self.client.get("/lecturer/courses")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CSC101", response.data)
        self.assertNotIn(b"CSC102", response.data)

    def test_lecturer_can_create_course(self):
        self.login("lecturer1@test.com", "LecturerPass123!")

        response = self.client.post(
            "/lecturer/courses/new",
            data={
                "course_code": "csc201",
                "title": "Software Engineering",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        created = Course.query.filter_by(course_code="CSC201").first()
        self.assertIsNotNone(created)
        self.assertEqual(created.lecturer_id, self.lecturer.id)

    def test_duplicate_course_code_is_rejected(self):
        self.login("lecturer1@test.com", "LecturerPass123!")

        response = self.client.post(
            "/lecturer/courses/new",
            data={
                "course_code": "CSC101",
                "title": "Another Course",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Course.query.filter_by(course_code="CSC101").count(),
            1,
        )

    def test_lecturer_cannot_edit_another_lecturers_course(self):
        self.login("lecturer1@test.com", "LecturerPass123!")

        response = self.client.get(
            f"/lecturer/courses/{self.other_course.id}/edit"
        )

        self.assertEqual(response.status_code, 403)

    def test_lecturer_can_enrol_student(self):
        self.login("lecturer1@test.com", "LecturerPass123!")

        response = self.client.post(
            f"/lecturer/courses/{self.course.id}/enrol",
            data={"student_id": str(self.student.id)},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        enrollment = Enrollment.query.filter_by(
            student_id=self.student.id,
            course_id=self.course.id,
        ).first()

        self.assertIsNotNone(enrollment)

    def test_student_cannot_manage_courses(self):
        self.login("student1@test.com", "StudentPass123!")

        response = self.client.get("/lecturer/courses")

        self.assertEqual(response.status_code, 403)

    def test_admin_can_view_all_courses(self):
        self.login("admin@test.com", "AdminPass123!")

        response = self.client.get("/admin/courses")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CSC101", response.data)
        self.assertIn(b"CSC102", response.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
