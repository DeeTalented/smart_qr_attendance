
import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from flask import (
    Blueprint,
    abort,
    current_app,
    make_response,
    render_template,
    request,
)
from flask_login import current_user

from decorators import role_required
from extensions import db
from models import (
    LOCAL_TIMEZONE,
    AttendanceRecord,
    AttendanceSession,
    Course,
    Enrollment,
    to_local,
)


reports_bp = Blueprint("reports", __name__)


# --- Register calculation --------------------------------------------------
#
# Rules (kept in one place so they are easy to explain and test):
#  1. Only finished sessions (closed or expired) count. A session that is
#     still open would otherwise lower everyone's percentage mid-class.
#  2. A session counts for a student only if it started after they enrolled
#     (they could not have attended before), unless they did attend it.
#  3. Percentage = sessions attended / sessions that count for the student.
#     With no counting sessions, the percentage is undefined (shown as "—").
#  4. Date filters use the local (WAT) calendar date of the session start.

PRESENT, ABSENT, NOT_APPLICABLE = "present", "absent", "n/a"


@dataclass
class RegisterRow:
    student: object
    marks: list = field(default_factory=list)

    @property
    def attended(self):
        return self.marks.count(PRESENT)

    @property
    def eligible(self):
        return sum(1 for mark in self.marks if mark != NOT_APPLICABLE)

    @property
    def percentage(self):
        if self.eligible == 0:
            return None

        return round(100 * self.attended / self.eligible, 1)


@dataclass
class Register:
    course: object
    sessions: list
    rows: list
    start: date = None
    end: date = None
    open_sessions_excluded: int = 0

    @property
    def average_percentage(self):
        values = [row.percentage for row in self.rows if row.percentage is not None]

        if not values:
            return None

        return round(sum(values) / len(values), 1)


def local_date_to_utc(day):
    """Midnight at the start of a local (WAT) date, as naive UTC."""
    local_midnight = datetime.combine(day, time.min, tzinfo=LOCAL_TIMEZONE)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def build_register(course, start=None, end=None, student_id=None):
    """Calculate the attendance register for a course.

    `start` and `end` are inclusive local dates. `student_id` limits the
    rows to one student (used for the student's own summary page).
    """
    query = db.select(AttendanceSession).where(
        AttendanceSession.course_id == course.id
    )

    if start is not None:
        query = query.where(AttendanceSession.started_at >= local_date_to_utc(start))

    if end is not None:
        query = query.where(
            AttendanceSession.started_at < local_date_to_utc(end + timedelta(days=1))
        )

    all_sessions = db.session.scalars(
        query.order_by(AttendanceSession.started_at)
    ).all()

    sessions = [s for s in all_sessions if not s.is_valid()]
    open_excluded = len(all_sessions) - len(sessions)

    enrol_query = db.select(Enrollment).where(Enrollment.course_id == course.id)

    if student_id is not None:
        enrol_query = enrol_query.where(Enrollment.student_id == student_id)

    enrollments = db.session.scalars(enrol_query).all()

    present_pairs = set()

    if sessions:
        present_pairs = set(
            db.session.execute(
                db.select(AttendanceRecord.session_id, AttendanceRecord.student_id)
                .where(AttendanceRecord.session_id.in_([s.id for s in sessions]))
            ).all()
        )

    rows = []

    for enrollment in enrollments:
        row = RegisterRow(student=enrollment.student)

        for attendance_session in sessions:
            if (attendance_session.id, enrollment.student_id) in present_pairs:
                row.marks.append(PRESENT)
            elif attendance_session.started_at < enrollment.enrolled_at:
                row.marks.append(NOT_APPLICABLE)
            else:
                row.marks.append(ABSENT)

        rows.append(row)

    rows.sort(key=lambda r: (r.student.full_name.lower(), r.student.id))

    return Register(
        course=course,
        sessions=sessions,
        rows=rows,
        start=start,
        end=end,
        open_sessions_excluded=open_excluded,
    )


# --- Request helpers -------------------------------------------------------

def parse_date_filters():
    """Read ?start=YYYY-MM-DD&end=YYYY-MM-DD. Returns (start, end, errors)."""
    errors = []
    values = {}

    for name in ("start", "end"):
        raw = request.args.get(name, "").strip()

        if not raw:
            values[name] = None
            continue

        try:
            values[name] = date.fromisoformat(raw)
        except ValueError:
            values[name] = None
            errors.append(f"Ignored invalid {name} date '{raw}'. Use YYYY-MM-DD.")

    start, end = values["start"], values["end"]

    if start and end and start > end:
        errors.append("The start date is after the end date, so no dates were applied.")
        start = end = None

    return start, end, errors


def get_viewable_course(course_id):
    """Admins may view any course; lecturers only their own."""
    course = db.session.get(Course, course_id)

    if course is None:
        abort(404)

    if current_user.role == "lecturer" and course.lecturer_id != current_user.id:
        abort(403)

    return course


def csv_safe(value):
    """Stop spreadsheet programs treating a cell as a formula.

    A student name such as "=HYPERLINK(...)" would otherwise run as a
    formula when a lecturer opens the export in Excel (CSV injection).
    """
    text = "" if value is None else str(value)

    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text

    return text


def format_percentage(value):
    return "" if value is None else f"{value:.1f}"


# --- Routes ----------------------------------------------------------------

@reports_bp.route("/courses/<int:course_id>/register")
@role_required("lecturer", "admin")
def course_register(course_id):
    course = get_viewable_course(course_id)
    start, end, errors = parse_date_filters()
    register = build_register(course, start, end)

    return render_template(
        "register.html",
        register=register,
        errors=errors,
        threshold=current_app.config["ATTENDANCE_THRESHOLD"],
    )


@reports_bp.route("/courses/<int:course_id>/register.csv")
@role_required("lecturer", "admin")
def course_register_csv(course_id):
    course = get_viewable_course(course_id)
    start, end, _ = parse_date_filters()
    register = build_register(course, start, end)

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    # Email addresses are left out on purpose: the register only needs
    # enough to identify the student (data minimisation).
    writer.writerow(
        ["Student number", "Full name"]
        + [to_local(s.started_at).strftime("%Y-%m-%d %H:%M") for s in register.sessions]
        + ["Attended", "Sessions counted", "Percentage"]
    )

    labels = {PRESENT: "Present", ABSENT: "Absent", NOT_APPLICABLE: "N/A"}

    for row in register.rows:
        writer.writerow(
            [csv_safe(row.student.student_number), csv_safe(row.student.full_name)]
            + [labels[mark] for mark in row.marks]
            + [row.attended, row.eligible, format_percentage(row.percentage)]
        )

    period = f"{start or 'all'}_to_{end or 'all'}"
    filename = f"{course.course_code}_attendance_{period}.csv"

    # The byte-order mark lets Excel read names with accents (e.g. Yoruba
    # tone marks) correctly.
    response = make_response("﻿" + buffer.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "no-store"
    return response


@reports_bp.route("/student/attendance")
@role_required("student")
def student_attendance():
    enrollments = db.session.scalars(
        db.select(Enrollment)
        .join(Course)
        .where(Enrollment.student_id == current_user.id)
        .order_by(Course.course_code)
    ).all()

    summaries = []

    for enrollment in enrollments:
        register = build_register(enrollment.course, student_id=current_user.id)
        row = register.rows[0] if register.rows else RegisterRow(current_user)
        # Newest session first, each paired with this student's mark.
        history = list(zip(register.sessions, row.marks))[::-1]
        summaries.append((register, row, history))

    return render_template(
        "student_attendance.html",
        summaries=summaries,
        threshold=current_app.config["ATTENDANCE_THRESHOLD"],
    )
