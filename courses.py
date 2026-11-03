from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    url_for,
)
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    User,
    Course,
    Enrollment,
    AttendanceRecord,
    AttendanceSession,
)
from forms import (
    AdminCourseForm,
    CourseForm,
    EnrollmentForm,
    StartSessionForm,
)
from decorators import role_required


courses_bp = Blueprint("courses", __name__)


def student_choices(students):
    """Format student accounts as (id, label) pairs for a select field."""
    return [
        (student.id, f"{student.full_name} ({student.email})")
        for student in students
    ]


def get_owned_course(course_id):
    """Return a course only if the current lecturer owns it."""
    course = db.session.get(Course, course_id)

    if course is None:
        abort(404)

    if course.lecturer_id != current_user.id:
        abort(403)

    return course


@courses_bp.route("/lecturer/courses")
@role_required("lecturer")
def lecturer_courses():
    courses = Course.query.filter_by(
        lecturer_id=current_user.id
    ).order_by(Course.course_code).all()

    return render_template(
        "course_list.html",
        courses=courses,
        page_title="My Courses",
        is_admin=False,
    )


@courses_bp.route("/lecturer/courses/new", methods=["GET", "POST"])
@role_required("lecturer")
def create_course():
    form = CourseForm()

    if form.validate_on_submit():
        course_code = form.course_code.data.strip().upper()
        title = form.title.data.strip()

        if Course.query.filter_by(course_code=course_code).first():
            form.course_code.errors.append(
                "This course code already exists."
            )
        else:
            course = Course(
                course_code=course_code,
                title=title,
                lecturer_id=current_user.id,
            )

            db.session.add(course)

            try:
                db.session.commit()
            except IntegrityError:
                # Another request created the same code in the meantime.
                db.session.rollback()
                form.course_code.errors.append(
                    "This course code already exists."
                )
            else:
                flash("Course created successfully.", "success")
                return redirect(url_for("courses.lecturer_courses"))

    return render_template(
        "course_form.html",
        form=form,
        page_title="Create Course",
    )


@courses_bp.route(
    "/lecturer/courses/<int:course_id>/edit",
    methods=["GET", "POST"],
)
@role_required("lecturer")
def edit_course(course_id):
    course = get_owned_course(course_id)
    form = CourseForm(obj=course)

    if form.validate_on_submit():
        course_code = form.course_code.data.strip().upper()
        title = form.title.data.strip()

        duplicate = Course.query.filter(
            Course.course_code == course_code,
            Course.id != course.id,
        ).first()

        if duplicate:
            form.course_code.errors.append(
                "Another course already uses this code."
            )
        else:
            course.course_code = course_code
            course.title = title

            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.course_code.errors.append(
                    "Another course already uses this code."
                )
            else:
                flash("Course updated successfully.", "success")
                return redirect(
                    url_for("courses.lecturer_courses")
                )

    return render_template(
        "course_form.html",
        form=form,
        page_title="Edit Course",
    )


@courses_bp.route(
    "/lecturer/courses/<int:course_id>/delete",
    methods=["POST"],
)
@role_required("lecturer")
def delete_course(course_id):
    course = get_owned_course(course_id)

    # Keep historical enrolment and attendance data intact.
    if course.enrollments or course.attendance_sessions:
        flash(
            "This course has enrolments or attendance sessions. "
            "It cannot be deleted; keep it for record integrity.",
            "error",
        )
        return redirect(url_for("courses.lecturer_courses"))

    db.session.delete(course)
    db.session.commit()

    flash("Course deleted successfully.", "success")
    return redirect(url_for("courses.lecturer_courses"))


@courses_bp.route("/lecturer/courses/<int:course_id>")
@role_required("lecturer")
def course_detail(course_id):
    course = get_owned_course(course_id)

    form = EnrollmentForm()

    students = User.query.filter_by(
        role="student"
    ).order_by(User.full_name).all()

    enrolled_ids = {
        enrollment.student_id
        for enrollment in course.enrollments
    }

    available_students = [
        student for student in students
        if student.id not in enrolled_ids
    ]

    # Only offer students who are not already enrolled.
    form.student_id.choices = student_choices(available_students)

    sessions = sorted(
        course.attendance_sessions,
        key=lambda s: s.started_at,
        reverse=True,
    )
    open_session = next((s for s in sessions if s.is_valid()), None)

    # "Present X / Y" per session, where Y follows the register's rule:
    # students enrolled when the session started, plus anyone who attended.
    enrolled_at = {e.student_id: e.enrolled_at for e in course.enrollments}
    session_counts = {}

    for s in sessions:
        present_ids = {record.student_id for record in s.attendance_records}
        expected = sum(
            1 for student_id, joined in enrolled_at.items()
            if joined <= s.started_at or student_id in present_ids
        )
        session_counts[s.id] = (len(present_ids), expected)

    return render_template(
        "course_detail.html",
        course=course,
        form=form,
        available_students=available_students,
        start_form=StartSessionForm(),
        sessions=sessions,
        open_session=open_session,
        session_counts=session_counts,
    )


@courses_bp.route(
    "/lecturer/courses/<int:course_id>/enrol",
    methods=["POST"],
)
@role_required("lecturer")
def enrol_student(course_id):
    course = get_owned_course(course_id)
    form = EnrollmentForm()

    students = User.query.filter_by(
        role="student"
    ).order_by(User.full_name).all()

    form.student_id.choices = student_choices(students)

    if not form.validate_on_submit():
        flash("Please select a valid student.", "error")
        return redirect(
            url_for("courses.course_detail", course_id=course.id)
        )

    student = db.session.get(User, form.student_id.data)

    if student is None or student.role != "student":
        flash("Only student accounts can be enrolled.", "error")
        return redirect(
            url_for("courses.course_detail", course_id=course.id)
        )

    existing = Enrollment.query.filter_by(
        student_id=student.id,
        course_id=course.id,
    ).first()

    if existing:
        flash("This student is already enrolled.", "error")
    else:
        db.session.add(
            Enrollment(
                student_id=student.id,
                course_id=course.id,
            )
        )

        try:
            db.session.commit()
            flash("Student enrolled successfully.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("The enrolment already exists.", "error")

    return redirect(
        url_for("courses.course_detail", course_id=course.id)
    )


@courses_bp.route(
    "/lecturer/courses/<int:course_id>/unenrol/<int:student_id>",
    methods=["POST"],
)
@role_required("lecturer")
def unenrol_student(course_id, student_id):
    course = get_owned_course(course_id)

    enrollment = Enrollment.query.filter_by(
        student_id=student_id,
        course_id=course.id,
    ).first()

    if enrollment is None:
        abort(404)

    # Do not remove enrolments if attendance has already been recorded.
    # The join keeps the check limited to sessions of this course.
    attendance_exists = db.session.scalar(
        select(AttendanceRecord.id)
        .join(
            AttendanceSession,
            AttendanceRecord.session_id == AttendanceSession.id,
        )
        .where(
            AttendanceSession.course_id == course.id,
            AttendanceRecord.student_id == student_id,
        )
        .limit(1)
    )

    if attendance_exists is not None:
        flash(
            "This student has attendance history for the course. "
            "The enrolment cannot be removed.",
            "error",
        )
    else:
        db.session.delete(enrollment)
        db.session.commit()
        flash("Student unenrolled successfully.", "success")

    return redirect(
        url_for("courses.course_detail", course_id=course.id)
    )


@courses_bp.route("/admin/courses")
@role_required("admin")
def admin_courses():
    courses = Course.query.order_by(Course.course_code).all()

    return render_template(
        "course_list.html",
        courses=courses,
        page_title="All Courses",
        is_admin=True,
    )


def lecturer_choices():
    lecturers = User.query.filter_by(
        role="lecturer"
    ).order_by(User.full_name).all()

    return [
        (lecturer.id, f"{lecturer.full_name} ({lecturer.email})")
        for lecturer in lecturers
    ]


def save_admin_course(form, course):
    """Validate and save an administrator's course form.

    Returns True when the course was saved. On failure, errors are added
    to the form and nothing is committed.
    """
    course_code = form.course_code.data.strip().upper()

    duplicate = Course.query.filter(
        Course.course_code == course_code,
        Course.id != course.id,
    ).first()

    if duplicate:
        form.course_code.errors.append("Another course already uses this code.")
        return False

    # The select field only offers lecturers, but check on the server too.
    lecturer = db.session.get(User, form.lecturer_id.data)

    if lecturer is None or lecturer.role != "lecturer":
        form.lecturer_id.errors.append("Choose a lecturer account.")
        return False

    course.course_code = course_code
    course.title = form.title.data.strip()
    course.lecturer_id = lecturer.id

    if course.id is None:
        db.session.add(course)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        form.course_code.errors.append("Another course already uses this code.")
        return False

    return True


@courses_bp.route("/admin/courses/new", methods=["GET", "POST"])
@role_required("admin")
def admin_create_course():
    form = AdminCourseForm()
    form.lecturer_id.choices = lecturer_choices()

    if form.validate_on_submit() and save_admin_course(form, Course()):
        flash("Course created successfully.", "success")
        return redirect(url_for("courses.admin_courses"))

    return render_template(
        "course_form.html",
        form=form,
        page_title="Create Course",
        back_url=url_for("courses.admin_courses"),
    )


@courses_bp.route(
    "/admin/courses/<int:course_id>/edit",
    methods=["GET", "POST"],
)
@role_required("admin")
def admin_edit_course(course_id):
    course = db.session.get(Course, course_id)

    if course is None:
        abort(404)

    form = AdminCourseForm(obj=course)
    form.lecturer_id.choices = lecturer_choices()

    if form.validate_on_submit() and save_admin_course(form, course):
        flash("Course updated successfully.", "success")
        return redirect(url_for("courses.admin_courses"))

    return render_template(
        "course_form.html",
        form=form,
        page_title="Edit Course",
        back_url=url_for("courses.admin_courses"),
    )
