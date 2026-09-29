from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from .extensions import db, login_manager
from .models import User
from .security import validate_csrf
from .services.audit import log_change


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))


def roles_required(*roles: str):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        validate_csrf()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and check_password_hash(user.password_hash, password):
            login_user(user)
            log_change("login", "user", user.id)
            from .extensions import db
            db.session.commit()
            return redirect(url_for("main.dashboard"))
        flash("Invalid username or password.", "error")

    return render_template("auth/login.html")


@auth_bp.post("/logout")
@login_required
def logout():
    validate_csrf()
    log_change("logout", "user", current_user.id)
    from .extensions import db
    db.session.commit()
    logout_user()
    return redirect(url_for("auth.login"))
