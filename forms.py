
from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    PasswordField,
    SubmitField,
    SelectField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    Optional,
)


# Minimum password length for any account. Kept in one place so the
# policy is easy to explain and change.
MIN_PASSWORD_LENGTH = 8

ROLE_CHOICES = [
    ("student", "Student"),
    ("lecturer", "Lecturer"),
    ("admin", "Administrator"),
]


class LoginForm(FlaskForm):
    email = StringField(
        "Email address",
        validators=[
            DataRequired(),
            Email(),
            Length(max=150),
        ],
    )

    password = PasswordField(
        "Password",
        validators=[DataRequired()],
    )

    submit = SubmitField("Sign in")


class CourseForm(FlaskForm):
    course_code = StringField(
        "Course code",
        validators=[
            DataRequired(),
            Length(min=2, max=30),
        ],
    )

    title = StringField(
        "Course title",
        validators=[
            DataRequired(),
            Length(min=3, max=150),
        ],
    )

    submit = SubmitField("Save course")


class AdminCourseForm(CourseForm):
    """Course form for administrators, who also choose the lecturer."""

    lecturer_id = SelectField(
        "Lecturer",
        coerce=int,
        validators=[DataRequired()],
    )


class EnrollmentForm(FlaskForm):
    student_id = SelectField(
        "Select student",
        coerce=int,
        validators=[DataRequired()],
    )

    submit = SubmitField("Enrol student")


class StartSessionForm(FlaskForm):
    duration_minutes = SelectField(
        "Accept attendance for",
        coerce=int,
        choices=[
            (5, "5 minutes"),
            (10, "10 minutes"),
            (15, "15 minutes"),
            (30, "30 minutes"),
            (60, "1 hour"),
        ],
        default=10,
    )

    submit = SubmitField("Start attendance session")


class UserDetailsForm(FlaskForm):
    """Fields shared by creating and editing a user."""

    full_name = StringField(
        "Full name",
        validators=[DataRequired(), Length(min=2, max=120)],
    )

    email = StringField(
        "Email address",
        validators=[DataRequired(), Email(), Length(max=150)],
    )

    student_number = StringField(
        "Matriculation / student number",
        validators=[Optional(), Length(max=50)],
    )

    submit = SubmitField("Save user")


class UserCreateForm(UserDetailsForm):
    role = SelectField(
        "Role",
        choices=ROLE_CHOICES,
        validators=[DataRequired()],
    )

    password = PasswordField(
        "Initial password",
        validators=[
            DataRequired(),
            Length(min=MIN_PASSWORD_LENGTH, max=128),
        ],
    )

    confirm_password = PasswordField(
        "Confirm password",
        validators=[
            DataRequired(),
            EqualTo("password", message="Passwords must match."),
        ],
    )


class PasswordResetForm(FlaskForm):
    """Used by an administrator to set a new password for a user."""

    password = PasswordField(
        "New password",
        validators=[
            DataRequired(),
            Length(min=MIN_PASSWORD_LENGTH, max=128),
        ],
    )

    confirm_password = PasswordField(
        "Confirm new password",
        validators=[
            DataRequired(),
            EqualTo("password", message="Passwords must match."),
        ],
    )

    submit = SubmitField("Set password")


class ChangePasswordForm(PasswordResetForm):
    """Used by any signed-in user to change their own password."""

    current_password = PasswordField(
        "Current password",
        validators=[DataRequired()],
    )

    submit = SubmitField("Change password")
