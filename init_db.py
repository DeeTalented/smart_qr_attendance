
"""Create the database tables and, optionally, sample development data.

    python init_db.py                 # create tables only
    python init_db.py --seed          # also add sample data (random password)
    python init_db.py --seed --password "YourDemoPass1"

All sample people are fictitious and use @example.com addresses, a domain
reserved for documentation, so no real personal data is ever created.
"""

import argparse
import os
import random
import secrets
import sys
from datetime import datetime, time, timedelta, timezone

from app import create_app
from extensions import db
from models import (
    LOCAL_TIMEZONE,
    AttendanceRecord,
    AttendanceSession,
    Course,
    Enrollment,
    User,
    utc_now,
)


STAFF = [
    ("Demo Administrator", "admin@example.com", "admin"),
    ("Demo Lecturer", "lecturer@example.com", "lecturer"),
    ("Demo Lecturer Two", "lecturer2@example.com", "lecturer"),
]

# (name, email, student number, how likely they are to attend)
STUDENTS = [
    ("Demo Student", "student@example.com", "DEMO001", 0.95),
    ("Adaeze Okafor", "student02@example.com", "DEMO002", 0.90),
    ("Babatunde Lawal", "student03@example.com", "DEMO003", 0.85),
    ("Chiamaka Nwosu", "student04@example.com", "DEMO004", 0.60),
    ("Damilola Adebayo", "student05@example.com", "DEMO005", 0.80),
    ("Emeka Eze", "student06@example.com", "DEMO006", 0.45),
    ("Funmilayo Ogunleye", "student07@example.com", "DEMO007", 0.92),
    ("Ibrahim Musa", "student08@example.com", "DEMO008", 0.75),
]

# (code, title, lecturer email, student numbers, past weekly sessions)
COURSES = [
    ("CSC101", "Introduction to Computer Science", "lecturer@example.com",
     [s[2] for s in STUDENTS], 6),
    ("CSC201", "Data Structures", "lecturer@example.com",
     ["DEMO001", "DEMO002", "DEMO003", "DEMO004", "DEMO005"], 4),
    ("CSC305", "Software Engineering", "lecturer2@example.com",
     ["DEMO001", "DEMO006", "DEMO007", "DEMO008"], 3),
]

# This student joins CSC101 late, to show sessions held before enrolment
# being marked "not counted" on the register.
LATE_ENROLMENT = ("DEMO008", "CSC101", 3)  # joins before week 3's class


def past_class_time(weeks_ago):
    """10:00 WAT on the same weekday, `weeks_ago` weeks back, as naive UTC."""
    day = (utc_now() + timedelta(hours=1)).date() - timedelta(weeks=weeks_ago)
    local = datetime.combine(day, time(10, 0), tzinfo=LOCAL_TIMEZONE)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def get_or_create_user(full_name, email, role, password, student_number=None):
    """Return (user, created). Existing accounts are left unchanged."""
    user = User.query.filter_by(email=email).first()

    if user is not None:
        return user, False

    user = User(
        full_name=full_name,
        email=email,
        role=role,
        student_number=student_number,
    )
    user.set_password(password)
    db.session.add(user)
    return user, True


def seed_sample_data(password, rng_seed=2026):
    """Add sample data. Safe to run more than once.

    Returns the list of email addresses of accounts created by this run.
    """
    rng = random.Random(rng_seed)
    created = []

    users = {}
    for full_name, email, role in STAFF:
        users[email], new = get_or_create_user(full_name, email, role, password)
        if new:
            created.append(email)

    students = {}
    attendance_rate = {}
    for full_name, email, number, rate in STUDENTS:
        student, new = get_or_create_user(full_name, email, "student", password, number)
        students[number] = student
        attendance_rate[number] = rate
        if new:
            created.append(email)

    db.session.flush()

    for code, title, lecturer_email, numbers, weeks in COURSES:
        course = Course.query.filter_by(course_code=code).first()

        if course is None:
            course = Course(
                course_code=code,
                title=title,
                lecturer_id=users[lecturer_email].id,
            )
            db.session.add(course)
            db.session.flush()

        for number in numbers:
            student = students[number]
            exists = Enrollment.query.filter_by(
                student_id=student.id, course_id=course.id
            ).first()

            if exists is None:
                enrolled_at = past_class_time(weeks + 1)

                if (number, code) == LATE_ENROLMENT[:2]:
                    enrolled_at = past_class_time(LATE_ENROLMENT[2]) - timedelta(days=1)

                db.session.add(Enrollment(
                    student_id=student.id,
                    course_id=course.id,
                    enrolled_at=enrolled_at,
                ))

        db.session.flush()

        # Only add history to a course that has never had a session, so
        # running the script twice does not double the data.
        if course.attendance_sessions:
            continue

        enrollments = Enrollment.query.filter_by(course_id=course.id).all()

        for weeks_ago in range(weeks, 0, -1):
            started = past_class_time(weeks_ago)
            attendance_session = AttendanceSession(
                course_id=course.id,
                created_by=course.lecturer_id,
                started_at=started,
                expires_at=started + timedelta(minutes=15),
                is_active=False,
            )
            # A random token is still required; it is never shown or used.
            attendance_session.issue_token()
            db.session.add(attendance_session)
            db.session.flush()

            for enrollment in enrollments:
                if enrollment.enrolled_at > started:
                    continue

                rate = attendance_rate.get(enrollment.student.student_number, 0.8)

                if rng.random() < rate:
                    db.session.add(AttendanceRecord(
                        session_id=attendance_session.id,
                        student_id=enrollment.student_id,
                        marked_at=started + timedelta(minutes=rng.randint(1, 12)),
                    ))

    db.session.commit()
    return created


def looks_like_production():
    """A real SECRET_KEY without debug mode suggests a live deployment."""
    return bool(os.environ.get("SECRET_KEY")) and os.environ.get("FLASK_DEBUG") != "1"


def main():
    parser = argparse.ArgumentParser(
        description="Create the Smart QR Attendance database."
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Also insert sample accounts, courses, enrolments and attendance.",
    )
    parser.add_argument(
        "--password",
        help="Password for newly created sample accounts "
             "(default: a random one, printed once).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow --seed even when the environment looks like production.",
    )
    args = parser.parse_args()

    if args.seed and looks_like_production() and not args.force:
        sys.exit(
            "Refusing to add demo accounts: SECRET_KEY is set and FLASK_DEBUG "
            "is not 1, so this looks like a production environment. "
            "Use --force only if you are sure."
        )

    if args.password is not None and len(args.password) < 8:
        sys.exit("The sample password must be at least 8 characters.")

    # This script never handles logins, so a throwaway key is enough here.
    app = create_app({"SECRET_KEY": secrets.token_hex(32)})

    with app.app_context():
        db.create_all()

        print("Database tables are ready.")
        print(f"Database: {db.engine.url.database}")

        if args.seed:
            password = args.password or secrets.token_urlsafe(9)
            created = seed_sample_data(password)

            print("Sample data added (existing records were left unchanged).")

            if created:
                print(f"New sample accounts ({len(created)}):")
                for email in created:
                    print(f"  {email}")
                if args.password is None:
                    print(f"Their password (shown once, not stored): {password}")
                else:
                    print("Their password is the one you supplied.")
            else:
                print("No new accounts were needed; all sample accounts already exist.")

            print("Sample accounts are for local development only.")


if __name__ == "__main__":
    main()
