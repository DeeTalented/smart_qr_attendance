
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, session, url_for
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)

from extensions import db
from forms import ChangePasswordForm, LoginForm
from models import User


auth_bp = Blueprint("auth", __name__)


def safe_next_url(target):
    """Allow only redirects to a path on this site.

    Rejects absolute URLs ("https://evil.example") and scheme-relative
    ones ("//evil.example"), which would make the login page an open
    redirect that attackers could use in phishing links.
    """
    if not target:
        return None

    parts = urlsplit(target)

    if (
        parts.scheme
        or parts.netloc
        or not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
    ):
        return None

    return target


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    form = LoginForm()

    if form.validate_on_submit():
        email = form.email.data.strip().lower()

        user = db.session.execute(
            db.select(User).where(db.func.lower(User.email) == email)
        ).scalar_one_or_none()

        if user is not None and user.check_password(form.password.data):
            login_user(user)
            flash("You have logged in successfully.", "success")

            # Flask-Login stores the page the user originally asked for
            # (for example a scanned QR link) in the session.
            next_url = safe_next_url(session.pop("next", None))
            return redirect(next_url or url_for("dashboard"))

        flash("Invalid email address or password.", "error")

    return render_template("login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    # The form's CSRF token is validated by Flask-WTF.
    if current_user.is_authenticated:
        logout_user()
        # Clear everything else in the session too, including any QR
        # tokens a lecturer was displaying, so nothing carries over to the
        # next person who signs in on a shared computer.
        session.clear()
        flash("You have logged out.", "info")

    return redirect(url_for("auth.login"))


@auth_bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()

    if form.validate_on_submit():
        # Re-check the current password so a session left open on a shared
        # computer cannot be used to take over the account.
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append(
                "Your current password is incorrect."
            )
        else:
            current_user.set_password(form.password.data)
            db.session.commit()

            flash("Your password has been changed.", "success")
            return redirect(url_for("dashboard"))

    return render_template(
        "password_form.html",
        form=form,
        page_title="Change Password",
        back_url=url_for("dashboard"),
    )
