
import base64
import io
from datetime import timedelta

import qrcode
import qrcode.image.svg
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from courses import get_owned_course
from decorators import role_required
from extensions import db
from forms import StartSessionForm
from models import (
    AttendanceRecord,
    AttendanceSession,
    Enrollment,
    utc_now,
)


attendance_bp = Blueprint("attendance", __name__)

# Raw tokens are kept in the lecturer's own signed session cookie so the
# QR code can be shown again after a page reload. Only a few are kept to
# stay well under the browser's cookie size limit.
TOKEN_SESSION_KEY = "qr_tokens"
MAX_REMEMBERED_TOKENS = 5


# --- Helpers ---------------------------------------------------------------

def open_session(course, lecturer, duration_minutes, now=None):
    """Create an attendance session and return (session, raw_token)."""
    if now is None:
        now = utc_now()

    attendance_session = AttendanceSession(
        course_id=course.id,
        created_by=lecturer.id,
        started_at=now,
        expires_at=now + timedelta(minutes=duration_minutes),
        is_active=True,
    )
    token = attendance_session.issue_token()

    db.session.add(attendance_session)
    db.session.commit()

    return attendance_session, token


def find_session_by_token(token):
    """Look up a session from a raw token, or return None."""
    return db.session.execute(
        db.select(AttendanceSession).where(
            AttendanceSession.qr_token_hash
            == AttendanceSession.hash_token(token)
        )
    ).scalar_one_or_none()


def remember_token(session_id, token):
    tokens = dict(session.get(TOKEN_SESSION_KEY, {}))
    tokens.pop(str(session_id), None)
    tokens[str(session_id)] = token

    # Dicts keep insertion order, so the oldest entries are dropped first.
    while len(tokens) > MAX_REMEMBERED_TOKENS:
        tokens.pop(next(iter(tokens)))

    session[TOKEN_SESSION_KEY] = tokens


def recall_token(attendance_session):
    """Return the remembered raw token if it is still the current one."""
    token = session.get(TOKEN_SESSION_KEY, {}).get(str(attendance_session.id))

    if token and AttendanceSession.hash_token(token) == attendance_session.qr_token_hash:
        return token

    return None


def get_owned_session(session_id):
    """Return an attendance session only if the current lecturer owns its course."""
    attendance_session = db.session.get(AttendanceSession, session_id)

    if attendance_session is None:
        abort(404)

    if attendance_session.course.lecturer_id != current_user.id:
        abort(403)

    return attendance_session


def qr_svg_data_uri(text):
    image = qrcode.make(
        text,
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=12,
        border=2,
    )
    buffer = io.BytesIO()
    image.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def scan_url(token):
    """The URL encoded in the QR code.

    PUBLIC_BASE_URL can be set when the lecturer's browser reaches the app
    through a different address from the one students' phones will use.
    """
    path = url_for("attendance.scan", token=token)
    base_url = current_app.config.get("PUBLIC_BASE_URL")

    if base_url:
        return base_url.rstrip("/") + path

    return url_for("attendance.scan", token=token, _external=True)


def no_store(response):
    """Pages that contain a live token must never be cached."""
    response.headers["Cache-Control"] = "no-store"
    return response


def present_count(attendance_session):
    return db.session.scalar(
        db.select(db.func.count(AttendanceRecord.id)).where(
            AttendanceRecord.session_id == attendance_session.id
        )
    )


# --- Lecturer routes -------------------------------------------------------

@attendance_bp.route(
    "/lecturer/courses/<int:course_id>/sessions/start",
    methods=["POST"],
)
@role_required("lecturer")
def start_session(course_id):
    course = get_owned_course(course_id)
    form = StartSessionForm()

    if not form.validate_on_submit():
        flash("Please choose a valid duration.", "error")
        return redirect(url_for("courses.course_detail", course_id=course.id))

    # Only one open session per course, so students cannot be confused by
    # two live QR codes for the same class.
    for existing in course.attendance_sessions:
        if existing.is_valid():
            flash("This course already has an open session.", "info")
            return redirect(
                url_for("attendance.session_detail", session_id=existing.id)
            )

    attendance_session, token = open_session(
        course, current_user, form.duration_minutes.data
    )
    remember_token(attendance_session.id, token)

    flash("Attendance session started. Display the QR code to the class.", "success")
    return redirect(
        url_for("attendance.session_detail", session_id=attendance_session.id)
    )


@attendance_bp.route("/lecturer/sessions/<int:session_id>")
@role_required("lecturer")
def session_detail(session_id):
    attendance_session = get_owned_session(session_id)

    token = recall_token(attendance_session) if attendance_session.is_valid() else None
    qr_image = qr_link = None

    if token:
        qr_link = scan_url(token)
        qr_image = qr_svg_data_uri(qr_link)

    records = db.session.scalars(
        db.select(AttendanceRecord)
        .where(AttendanceRecord.session_id == attendance_session.id)
        .order_by(AttendanceRecord.marked_at)
    ).all()

    enrolled_count = len(attendance_session.course.enrollments)

    response = make_response(render_template(
        "session_detail.html",
        attendance_session=attendance_session,
        records=records,
        enrolled_count=enrolled_count,
        qr_image=qr_image,
        qr_link=qr_link,
        seconds_left=max(
            0,
            int((attendance_session.expires_at - utc_now()).total_seconds()),
        ),
    ))
    return no_store(response)


@attendance_bp.route("/lecturer/sessions/<int:session_id>/status")
@role_required("lecturer")
def session_status(session_id):
    """Small JSON feed so the QR page can update its counter live."""
    attendance_session = get_owned_session(session_id)

    return no_store(jsonify(
        status=attendance_session.status,
        present=present_count(attendance_session),
        seconds_left=max(
            0,
            int((attendance_session.expires_at - utc_now()).total_seconds()),
        ),
    ))


@attendance_bp.route(
    "/lecturer/sessions/<int:session_id>/regenerate",
    methods=["POST"],
)
@role_required("lecturer")
def regenerate_token(session_id):
    attendance_session = get_owned_session(session_id)

    if not attendance_session.is_valid():
        flash("This session is no longer open.", "error")
    else:
        token = attendance_session.issue_token()
        db.session.commit()
        remember_token(attendance_session.id, token)
        flash("New QR code issued. The previous code no longer works.", "success")

    return redirect(
        url_for("attendance.session_detail", session_id=attendance_session.id)
    )


@attendance_bp.route(
    "/lecturer/sessions/<int:session_id>/close",
    methods=["POST"],
)
@role_required("lecturer")
def close_session(session_id):
    attendance_session = get_owned_session(session_id)

    if attendance_session.is_active:
        attendance_session.close()
        db.session.commit()
        flash("Attendance session closed.", "success")

    tokens = dict(session.get(TOKEN_SESSION_KEY, {}))
    if tokens.pop(str(attendance_session.id), None) is not None:
        session[TOKEN_SESSION_KEY] = tokens

    return redirect(
        url_for("attendance.session_detail", session_id=attendance_session.id)
    )


# --- Student routes --------------------------------------------------------

def check_scan(token):
    """Work out what should happen for this student and token.

    Returns (outcome, attendance_session, http_status). Every check runs
    on the server, using server time, on every request.
    """
    attendance_session = find_session_by_token(token)

    if attendance_session is None:
        return "invalid", None, 404

    if not attendance_session.is_active:
        return "closed", attendance_session, 410

    if not attendance_session.is_valid():
        return "expired", attendance_session, 410

    enrolled = db.session.execute(
        db.select(Enrollment.id).where(
            Enrollment.student_id == current_user.id,
            Enrollment.course_id == attendance_session.course_id,
        )
    ).first()

    if enrolled is None:
        return "not_enrolled", attendance_session, 403

    already = db.session.execute(
        db.select(AttendanceRecord.id).where(
            AttendanceRecord.session_id == attendance_session.id,
            AttendanceRecord.student_id == current_user.id,
        )
    ).first()

    if already is not None:
        return "already", attendance_session, 409

    return "confirm", attendance_session, 200


@attendance_bp.route("/attend/<token>", methods=["GET", "POST"])
@role_required("student")
def scan(token):
    outcome, attendance_session, status = check_scan(token)

    # GET only shows a confirmation page; attendance is recorded by a POST
    # carrying a CSRF token, so merely opening a link changes nothing.
    if request.method == "POST" and outcome == "confirm":
        db.session.add(
            AttendanceRecord(
                session_id=attendance_session.id,
                student_id=current_user.id,
                status="present",
            )
        )

        try:
            db.session.commit()
            outcome, status = "recorded", 201
        except IntegrityError:
            # A double-tap or second device got there first. The UNIQUE
            # constraint on (session_id, student_id) is the final guard.
            db.session.rollback()
            outcome, status = "already", 409

    response = make_response(
        render_template(
            "attend.html",
            outcome=outcome,
            attendance_session=attendance_session,
            token=token,
        ),
        status,
    )
    return no_store(response)
