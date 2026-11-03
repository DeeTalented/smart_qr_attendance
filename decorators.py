
from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def role_required(*allowed_roles):
    """Require login and one of the specified user roles."""

    def decorator(view_function):
        @wraps(view_function)
        @login_required
        def wrapped_view(*args, **kwargs):
            if current_user.role not in allowed_roles:
                abort(403)

            return view_function(*args, **kwargs)

        return wrapped_view

    return decorator
