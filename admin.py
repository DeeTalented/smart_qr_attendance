
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy.exc import IntegrityError

from decorators import role_required
from extensions import db
from forms import (
    PasswordResetForm,
    ROLE_CHOICES,
    UserCreateForm,
    UserDetailsForm,
)
from models import User


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

VALID_ROLES = {value for value, _ in ROLE_CHOICES}


def get_user_or_404(user_id):
    user = db.session.get(User, user_id)

    if user is None:
        abort(404)

    return user


def clean_user_details(form, role, user=None):
    """Normalise and validate the shared user fields.

    Returns (email, student_number) on success, or None after adding
    field errors to the form. `user` is the account being edited, so it
    is excluded from the uniqueness checks.
    """
    email = form.email.data.strip().lower()
    student_number = (form.student_number.data or "").strip().upper() or None
    valid = True

    email_owner = db.session.execute(
        db.select(User).where(db.func.lower(User.email) == email)
    ).scalar_one_or_none()

    if email_owner is not None and email_owner is not user:
        form.email.errors.append("Another account already uses this email.")
        valid = False

    if role == "student":
        if student_number is None:
            form.student_number.errors.append(
                "A student number is required for student accounts."
            )
            valid = False
        else:
            number_owner = db.session.execute(
                db.select(User).where(User.student_number == student_number)
            ).scalar_one_or_none()

            if number_owner is not None and number_owner is not user:
                form.student_number.errors.append(
                    "Another student already has this number."
                )
                valid = False
    else:
        # Staff accounts never carry a student number. Storing NULL rather
        # than an empty string also keeps the UNIQUE constraint correct.
        student_number = None

    return (email, student_number) if valid else None


@admin_bp.route("/users")
@role_required("admin")
def user_list():
    role = request.args.get("role", "")

    query = db.select(User).order_by(User.role, User.full_name)

    if role in VALID_ROLES:
        query = query.where(User.role == role)
    else:
        role = ""

    users = db.session.scalars(query).all()

    return render_template(
        "user_list.html",
        users=users,
        selected_role=role,
        role_choices=ROLE_CHOICES,
    )


@admin_bp.route("/users/new", methods=["GET", "POST"])
@role_required("admin")
def create_user():
    form = UserCreateForm()

    if form.validate_on_submit():
        cleaned = clean_user_details(form, form.role.data)

        if cleaned is not None:
            email, student_number = cleaned

            user = User(
                full_name=form.full_name.data.strip(),
                email=email,
                role=form.role.data,
                student_number=student_number,
            )
            user.set_password(form.password.data)
            db.session.add(user)

            try:
                db.session.commit()
            except IntegrityError:
                # A concurrent request took the email or number first.
                db.session.rollback()
                form.email.errors.append(
                    "This email or student number is already in use."
                )
            else:
                flash(f"Account created for {user.full_name}.", "success")
                return redirect(url_for("admin.user_list"))

    return render_template(
        "user_form.html",
        form=form,
        page_title="Create User",
        user=None,
    )


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def edit_user(user_id):
    user = get_user_or_404(user_id)
    form = UserDetailsForm(obj=user)

    if form.validate_on_submit():
        cleaned = clean_user_details(form, user.role, user=user)

        if cleaned is not None:
            user.full_name = form.full_name.data.strip()
            user.email, user.student_number = cleaned

            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.email.errors.append(
                    "This email or student number is already in use."
                )
            else:
                flash("User details updated.", "success")
                return redirect(url_for("admin.user_list"))

    return render_template(
        "user_form.html",
        form=form,
        page_title="Edit User",
        user=user,
    )


@admin_bp.route("/users/<int:user_id>/password", methods=["GET", "POST"])
@role_required("admin")
def reset_password(user_id):
    user = get_user_or_404(user_id)
    form = PasswordResetForm()

    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()

        flash(f"Password reset for {user.full_name}.", "success")
        return redirect(url_for("admin.user_list"))

    return render_template(
        "password_form.html",
        form=form,
        page_title=f"Reset Password — {user.full_name}",
        back_url=url_for("admin.user_list"),
    )
