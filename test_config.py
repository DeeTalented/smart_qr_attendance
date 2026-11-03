
import os
import tempfile
import unittest
from unittest import mock

from sqlalchemy.exc import IntegrityError

from app import create_app
from extensions import db
from models import Enrollment, User


# Never let these tests pick up the real database or a real key.
CLEAN_ENV = {"DATABASE_URL": "sqlite://"}


class SecretKeyTests(unittest.TestCase):

    def environment(self, **extra):
        env = {
            key: value for key, value in os.environ.items()
            if key not in ("SECRET_KEY", "FLASK_DEBUG")
        }
        env.update(CLEAN_ENV)
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_production_refuses_to_start_without_secret_key(self):
        with self.environment():
            with self.assertRaises(RuntimeError):
                create_app()

    def test_secret_key_is_read_from_environment(self):
        with self.environment(SECRET_KEY="from-environment-value"):
            app = create_app()

        self.assertEqual(app.config["SECRET_KEY"], "from-environment-value")

    def test_debug_mode_key_is_random_per_machine_and_survives_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            key_file = os.path.join(folder, "dev-secret-key")
            other_file = os.path.join(folder, "other-machine-key")

            with self.environment(FLASK_DEBUG="1"):
                first = create_app({"DEV_SECRET_KEY_FILE": key_file}).config["SECRET_KEY"]
                restarted = create_app({"DEV_SECRET_KEY_FILE": key_file}).config["SECRET_KEY"]
                other = create_app({"DEV_SECRET_KEY_FILE": other_file}).config["SECRET_KEY"]

            with open(key_file, encoding="ascii") as stored:
                self.assertEqual(stored.read(), first)

        self.assertEqual(len(first), 64)
        self.assertEqual(first, restarted)
        self.assertNotEqual(first, other)


class JournalModeTests(unittest.TestCase):

    def test_journal_mode_defaults_to_wal_and_rejects_unknown_values(self):
        from extensions import sqlite_journal_mode

        cases = {None: "WAL", "delete": "DELETE", "TRUNCATE": "TRUNCATE",
                 "OFF; DROP TABLE users": "WAL", "": "WAL"}

        for value, expected in cases.items():
            env = {k: v for k, v in os.environ.items() if k != "SQLITE_JOURNAL_MODE"}
            if value is not None:
                env["SQLITE_JOURNAL_MODE"] = value
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(sqlite_journal_mode(), expected, value)


class SecurityDefaultsTests(unittest.TestCase):

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
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

    def test_session_cookie_settings(self):
        self.assertTrue(self.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], "Lax")

    def test_foreign_keys_are_enforced(self):
        db.session.add(Enrollment(student_id=9999, course_id=9999))

        with self.assertRaises(IntegrityError):
            db.session.commit()

        db.session.rollback()

    def test_real_password_hashing_uses_scrypt(self):
        # No PASSWORD_HASH_METHOD here, as in the real application.
        user = User(full_name="X", email="x@test.com", role="admin")
        user.set_password("Password123!")

        self.assertTrue(user.password_hash.startswith("scrypt:"))

    def test_expired_form_shows_friendly_message(self):
        app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SECRET_KEY": "test-secret",
        })

        response = app.test_client().post("/logout", data={"csrf_token": "stale"})

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"This form has expired", response.data)

    def test_logout_requires_post(self):
        response = self.app.test_client().get("/logout")

        self.assertEqual(response.status_code, 405)

    def test_unknown_email_and_wrong_password_look_the_same(self):
        user = User(full_name="X", email="x@test.com", role="admin")
        user.set_password("Password123!")
        db.session.add(user)
        db.session.commit()

        client = self.app.test_client()
        unknown = client.post("/login", data={"email": "nobody@test.com", "password": "x"})
        wrong = client.post("/login", data={"email": "x@test.com", "password": "x"})

        self.assertIn(b"Invalid email address or password.", unknown.data)
        self.assertIn(b"Invalid email address or password.", wrong.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
