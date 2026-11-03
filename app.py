
import os
import secrets

from flask import Flask, redirect, render_template, url_for
from flask_login import current_user, login_required
from flask_wtf.csrf import CSRFError

from extensions import db, login_manager, csrf


def load_or_create_dev_key(path):
    """Read the development secret key, creating a random one if needed."""
    try:
        with open(path, encoding="ascii") as key_file:
            key = key_file.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass

    key = secrets.token_hex(32)
    with open(path, "w", encoding="ascii") as key_file:
        key_file.write(key)
    return key


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    os.makedirs(app.instance_path, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL",
            "sqlite:///" + os.path.join(app.instance_path, "attendance.db"),
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Set SESSION_COOKIE_SECURE=1 when the site is served over HTTPS.
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        # Keep the "return to" page in the session rather than in a ?next=
        # query string, so scanned QR tokens do not appear in login URLs.
        USE_SESSION_FOR_NEXT=True,
        # Optional address students' phones use to reach the server, e.g.
        # http://192.168.0.10:5000 when the lecturer browses via localhost.
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL"),
        # Percentage below which attendance is highlighted on registers
        # (many universities require 75% to sit the examination).
        ATTENDANCE_THRESHOLD=75,
    )

    if test_config:
        app.config.update(test_config)

    if not app.config["SECRET_KEY"]:
        if app.testing:
            app.config["SECRET_KEY"] = secrets.token_hex(32)
        elif app.debug:
            # A random key generated once per machine and kept in the
            # instance folder (never committed), so the auto-reloader does
            # not log everyone out. No fixed, publicly known key is used.
            app.config["SECRET_KEY"] = load_or_create_dev_key(
                app.config.get("DEV_SECRET_KEY_FILE")
                or os.path.join(app.instance_path, "dev-secret-key")
            )
        else:
            raise RuntimeError(
                "SECRET_KEY is not set. Set the SECRET_KEY environment "
                "variable before running in production, or run with "
                "--debug for local development."
            )

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access that page."
    login_manager.login_message_category = "info"

    # Import models so SQLAlchemy knows all the table definitions.
    from models import User, Course, Enrollment, AttendanceSession

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    # Register blueprints inside the factory so every app instance,
    # including test apps, has all routes.
    from admin import admin_bp
    from attendance import attendance_bp
    from auth import auth_bp
    from courses import courses_bp
    from decorators import role_required
    from models import to_local
    from reports import reports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(courses_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(reports_bp)

    @app.template_filter("local_time")
    def local_time(value, fmt="%d %b %Y, %H:%M"):
        """Show a stored UTC time in West Africa Time."""
        local = to_local(value)
        return local.strftime(fmt) if local else "—"

    @app.after_request
    def set_security_headers(response):
        # Stop other sites framing our pages (clickjacking the confirm
        # button), stop MIME sniffing, and keep URLs containing tokens
        # out of Referer headers sent to other sites.
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.route("/")
    def home():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))

        return redirect(url_for("auth.login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        if current_user.role == "admin":
            return redirect(url_for("admin_dashboard"))

        if current_user.role == "lecturer":
            return redirect(url_for("lecturer_dashboard"))

        if current_user.role == "student":
            return redirect(url_for("student_dashboard"))

        return render_template("forbidden.html"), 403

    # Dashboards pass three simple lists to one template:
    #   stats   — headline numbers (label, value, optional sub-text)
    #   courses — course cards (code, title, link, meta lines, optional %)
    #   actions — shortcut cards (link, title, text, icon name)

    @app.route("/admin/dashboard")
    @role_required("admin")
    def admin_dashboard():
        role_counts = dict(
            db.session.execute(
                db.select(User.role, db.func.count(User.id))
                .group_by(User.role)
            ).all()
        )
        course_count = db.session.scalar(db.select(db.func.count(Course.id)))
        session_count = db.session.scalar(
            db.select(db.func.count(AttendanceSession.id))
        )

        return render_template(
            "dashboard.html",
            page_title="Administrator Dashboard",
            description="Manage users, courses and attendance across the system.",
            stats=[
                dict(label="Students", value=role_counts.get("student", 0)),
                dict(label="Lecturers", value=role_counts.get("lecturer", 0)),
                dict(label="Courses", value=course_count),
                dict(label="Sessions held", value=session_count,
                     sub=f"{role_counts.get('admin', 0)} administrator account(s)"),
            ],
            actions=[
                dict(url=url_for("admin.create_user"), icon="plus",
                     title="Create a user", text="Add a student, lecturer or administrator."),
                dict(url=url_for("admin.user_list"), icon="users",
                     title="Manage users", text="Edit details and reset passwords."),
                dict(url=url_for("courses.admin_courses"), icon="book",
                     title="Manage courses", text="Create courses, assign lecturers, open registers."),
            ],
        )

    @app.route("/lecturer/dashboard")
    @role_required("lecturer")
    def lecturer_dashboard():
        courses = db.session.scalars(
            db.select(Course)
            .where(Course.lecturer_id == current_user.id)
            .order_by(Course.course_code)
        ).all()

        sessions = [s for course in courses for s in course.attendance_sessions]
        open_now = [s for s in sessions if s.is_valid()]

        cards = []
        for course in courses:
            live = next((s for s in course.attendance_sessions if s.is_valid()), None)
            cards.append(dict(
                code=course.course_code,
                title=course.title,
                url=url_for("courses.course_detail", course_id=course.id),
                meta=[
                    f"{len(course.enrollments)} students",
                    f"{len(course.attendance_sessions)} sessions",
                ],
                live=live is not None,
            ))

        return render_template(
            "dashboard.html",
            page_title="Lecturer Dashboard",
            description="Run QR attendance sessions and follow your registers.",
            stats=[
                dict(label="My courses", value=len(courses)),
                dict(label="Enrolments", value=sum(len(c.enrollments) for c in courses)),
                dict(label="Sessions held", value=len(sessions)),
                dict(label="Open now", value=len(open_now)),
            ],
            courses=cards,
            courses_heading="My courses",
            actions=[
                dict(url=url_for("courses.create_course"), icon="plus",
                     title="Create a course", text="Add a new course you teach."),
                dict(url=url_for("courses.lecturer_courses"), icon="book",
                     title="Manage courses", text="Enrol students and start sessions."),
            ],
        )

    @app.route("/student/dashboard")
    @role_required("student")
    def student_dashboard():
        from reports import build_register

        enrollments = db.session.scalars(
            db.select(Enrollment)
            .join(Course)
            .where(Enrollment.student_id == current_user.id)
            .order_by(Course.course_code)
        ).all()

        threshold = app.config["ATTENDANCE_THRESHOLD"]
        attended = eligible = 0
        cards = []

        for enrollment in enrollments:
            register = build_register(enrollment.course, student_id=current_user.id)
            row = register.rows[0]
            attended += row.attended
            eligible += row.eligible
            cards.append(dict(
                code=enrollment.course.course_code,
                title=enrollment.course.title,
                url=url_for("reports.student_attendance"),
                meta=[f"Attended {row.attended} of {row.eligible}"],
                percent=row.percentage,
                low=row.percentage is not None and row.percentage < threshold,
            ))

        overall = round(100 * attended / eligible, 1) if eligible else None

        return render_template(
            "dashboard.html",
            page_title="Student Dashboard",
            description="Scan the QR code shown in class to record your attendance.",
            stats=[
                dict(label="Courses", value=len(enrollments)),
                dict(label="Overall attendance",
                     value=f"{overall:.1f}%" if overall is not None else "—",
                     sub=f"Minimum required: {threshold}%"),
                dict(label="Classes attended", value=attended,
                     sub=f"out of {eligible} counted"),
            ],
            courses=cards,
            courses_heading="My courses",
            actions=[
                dict(url=url_for("reports.student_attendance"), icon="chart",
                     title="My attendance", text="See every session, course by course."),
            ],
        )

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("forbidden.html"), 403

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        # Usually a form left open too long, or opened before the server
        # restarted. Explain it rather than showing a bare "400" page.
        return render_template("csrf_error.html"), 400

    return app


if __name__ == "__main__":
    # Debug mode is controlled by the FLASK_DEBUG environment variable,
    # never hard-coded. Preferred: flask --app app run --debug
    create_app().run()
