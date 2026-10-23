import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user

from .extns import db
from .models import LoginFailure, User

auth = Blueprint("auth", __name__)
audit = logging.getLogger("audit")

MIN_PASSWORD_LENGTH = 8
# Wrong passwords are counted per address, so a stranger's guesses never lock the owner out:
MAX_FAILED_LOGINS = 5  # for one account from one address, before that address pauses for it
MAX_FAILED_LOGINS_PER_ADDRESS = 20  # across all accounts, before the address pauses (password spraying)
LOCK_MINUTES = 15

HOME_BY_ROLE = {
    "admin": "admin.dashboard",
    "project-admin": "manager.my_projects",
    "end-user": "annotation.dashboard",
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


def forget_failed_logins(user, address=None):
    """A right password clears that address's count for the account; a new one from the admin, every address's."""
    failures = LoginFailure.query.filter_by(user_id=user.id)
    if address is not None:
        failures = failures.filter_by(address=address)
    failures.delete()
    db.session.commit()


@auth.route("/", methods=["GET", "POST"])
@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        address = request.remote_addr or "unknown"
        # Only a real account is named: what was typed may be a password typed in the wrong box.
        who = f"user_id={user.id} email={user.email}" if user else "account=unknown"

        # Checked first, so during a pause even the right password is refused from this address.
        recent = LoginFailure.query.filter(LoginFailure.address == address,
                                           LoginFailure.at > now - timedelta(minutes=LOCK_MINUTES))
        if recent.count() >= MAX_FAILED_LOGINS_PER_ADDRESS:
            audit.info("LOGIN_THROTTLED %s ip=%s", who, address)
            flash(f"Too many failed sign-ins from your network. Try again in {LOCK_MINUTES} minutes.", "danger")
            return redirect(url_for("auth.login"))
        if user and recent.filter(LoginFailure.user_id == user.id).count() >= MAX_FAILED_LOGINS:
            audit.info("LOGIN_LOCKED %s ip=%s", who, address)
            flash(f"Too many wrong passwords. Try again in {LOCK_MINUTES} minutes.", "danger")
            return redirect(url_for("auth.login"))

        if not user or not user.active or not user.check_password(password):
            audit.info("LOGIN_FAILED %s ip=%s", who, address)
            LoginFailure.query.filter(LoginFailure.at <= now - timedelta(minutes=LOCK_MINUTES)).delete()
            db.session.add(LoginFailure(address=address, user_id=user.id if user else None, at=now))
            db.session.commit()
            flash("Email address or password is incorrect!", "danger")
            return redirect(url_for("auth.login"))

        forget_failed_logins(user, address)

        session.clear()
        login_user(user)
        session.permanent = True  # enables the idle timeout
        audit.info("LOGIN_OK user_id=%s email=%s ip=%s", user.id, user.email, request.remote_addr)

        return redirect(_safe_next(request.args.get("next"), user) or _home_for(user))

    if current_user.is_authenticated:
        return redirect(_home_for(current_user))
    return render_template("login.html")


@auth.route("/logout")
def logout():
    if current_user.is_authenticated:
        audit.info("LOGOUT user_id=%s", current_user.id)
        current_user.end_all_sessions()  # a copied cookie stops working too (on every device)
        db.session.commit()
        logout_user()
        session.clear()
        flash("Logged out!", "warning")
    return redirect(url_for("auth.login"))
