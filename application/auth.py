import logging
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user

from .models import User

auth = Blueprint("auth", __name__)
audit = logging.getLogger("audit")

MIN_PASSWORD_LENGTH = 6

HOME_BY_ROLE = {
    "admin": "admin.dashboard",
    "project-admin": "manager.my_projects",
    "end-user": "user.dashboard",
}


def _home_for(user):
    return url_for(HOME_BY_ROLE.get(user.role, "auth.login"))


# Which part of the site each role may be sent back to after signing in.
AREA_BY_ROLE = {"admin": "/admin/", "project-admin": "/admin/", "end-user": "/user/"}


def _safe_next(target, user):
    """Return ``target`` only if it is a page on this site the user may open.

    Prevents open redirects (no other domains) and avoids sending someone to
    a page their role cannot see, e.g. an annotator signing in from a link to
    an admin page that an earlier session left behind.
    """
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/"):
        return None
    if not parts.path.startswith(AREA_BY_ROLE.get(user.role, "/nowhere/")):
        return None
    return target


@auth.route("/", methods=["GET", "POST"])
@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            audit.info("LOGIN_FAILED email=%s ip=%s", email, request.remote_addr)
            flash("Email address or password is incorrect!", "danger")
            return redirect(url_for("auth.login"))

        session.clear()
        login_user(user)
        session.permanent = True  # enables the idle timeout
        # Kept for templates that display the signed-in user's details.
        session["id"] = user.id
        session["role"] = user.role
        session["name"] = user.full_name
        session["email"] = user.email
        audit.info("LOGIN_OK user_id=%s email=%s ip=%s", user.id, user.email, request.remote_addr)

        return redirect(_safe_next(request.args.get("next"), user) or _home_for(user))

    if current_user.is_authenticated:
        return redirect(_home_for(current_user))
    return render_template("login.html")


@auth.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        fullname = (request.form.get("fullname") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""

        if not fullname or not email:
            flash("Full name and email are required.", "danger")
            return redirect(url_for("auth.register"))

        if len(password) < MIN_PASSWORD_LENGTH:
            flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.", "danger")
            return redirect(url_for("auth.register"))

        if User.query.filter(User.email == email).first():
            flash("Email Address already exist!", "danger")
            return redirect(url_for("auth.register"))

        user = User()
        user.register_end_user(fullname, email, password)
        audit.info("REGISTER user_id=%s email=%s", user.id, email)

        flash("Account Created Successfully!", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@auth.route("/logout")
def logout():
    if current_user.is_authenticated:
        audit.info("LOGOUT user_id=%s", current_user.id)
        logout_user()
        session.clear()
        flash("Logged out!", "warning")
    return redirect(url_for("auth.login"))
