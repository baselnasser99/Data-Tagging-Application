import logging
import os
import posixpath
import re
from logging.handlers import RotatingFileHandler

try:
    import fcntl
except ImportError:  # Windows: only the development server runs there, as a single process
    fcntl = None

import pymysql
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

pymysql.install_as_MySQLdb()

try:  # Load a local .env file when python-dotenv is installed.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - optional dependency
    pass

from .config import Config  # noqa: E402  (must come after load_dotenv)
from .extns import csrf, db, login_manager  # noqa: E402
from .models import ProjectImage, User  # noqa: E402


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(config_object or Config)
    _refuse_known_secrets(app)

    _configure_logging(app)
    _configure_https(app)

    db.init_app(app)
    login_manager.init_app(app)
    _allow_big_uploads(app)  # before CSRF: its check reads the form, which must see the larger limit
    csrf.init_app(app)

    from .admin import admin
    from .auth import MIN_PASSWORD_LENGTH, auth
    from .annotation import annotation
    from .groups import groups
    from .manager import manager
    from .annotation_admin import annotation_admin
    from .permissions import can_open_upload, can_see_project
    from .uploads import upload_dir
    from .jinja_utils import getattr_safe, plural
    from .sidebar import sidebar_summary

    app.register_blueprint(auth)
    app.register_blueprint(admin)
    app.register_blueprint(groups)
    app.register_blueprint(manager)
    app.register_blueprint(annotation_admin)
    app.register_blueprint(annotation)

    app.jinja_env.globals["getattr_safe"] = getattr_safe
    app.jinja_env.globals["plural"] = plural
    app.jinja_env.globals["min_password_length"] = MIN_PASSWORD_LENGTH
    app.jinja_env.globals["sidebar_summary"] = sidebar_summary
    app.jinja_env.globals["app_name"] = app.config["APP_NAME"]

    _register_error_handlers(app)

    @app.before_request
    def _hide_uploads_from_static():
        # Uploads live under static/ but are served only to signed-in people, below.
        # The static view resolves "./", "../" and "%2E" before it opens the file, so the name is
        # resolved the same way first: "js/../uploads/x" is an upload too. Lower case: Windows and
        # macOS disks don't tell "UPLOADS" from "uploads".
        if request.endpoint == "static":
            name = posixpath.normpath((request.view_args or {}).get("filename", "").replace("\\", "/")).lower()
            if name == "uploads" or name.startswith("uploads/"):
                abort(404)

    @app.before_request
    def _replace_temporary_password():
        # An admin saw the temporary password, so its owner chooses their own before anything else.
        if not (current_user.is_authenticated and current_user.must_change_password):
            return None
        page = "annotation.change_password" if current_user.role == "end-user" else "admin.admin_profile"
        if request.endpoint in (page, "auth.logout", "static"):
            return None
        flash("Choose a new password to continue: you signed in with a temporary one.", "warning")
        return redirect(url_for(page))

    @app.route("/files/<path:name>")
    @login_required
    def uploaded_file(name):
        # Only admins, the project's managers and members of its groups (see permissions.py).
        if not can_open_upload(name):
            abort(403)
        return send_from_directory(upload_dir(), name)

    @app.route("/item/<int:item_id>")
    @login_required
    def item_file(item_id):
        # A project's item by its number, so the address doesn't carry the file name, which can
        # give the answer away ("red_stop_sign.png"). Same rule as above: the project's people only.
        item = db.session.get(ProjectImage, item_id)
        if item is None or not can_see_project(current_user, item.project_id):
            abort(403)  # unknown items too: no hint that one exists
        return send_from_directory(upload_dir("images", str(item.project_id)), item.filename)

    @app.after_request
    def _security_headers(response):
        # Pages show private data: don't let the browser keep a copy that the
        # Back button could show after sign-out. Static files may be cached.
        if request.endpoint not in ("static", "uploaded_file", "item_file"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        # Everything from our own server only: scripts (templates have no inline <script> or on*
        # handlers), stylesheets and fonts (the fonts are kept in static/vendor), images, audio, video.
        # Inline style="" attributes are still allowed: about 50 templates use them.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self'; media-src 'self'; connect-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    with app.app_context():
        db.create_all()
        _upgrade_schema(app)
        _retire_v1_default_admin(app)
        _ensure_admin(app)

    if app.config.get("SECRET_KEY_IS_GENERATED") and not app.testing:
        app.logger.warning(
            "SECRET_KEY is not set; using a random key. Users will be logged out "
            "on every restart. Set SECRET_KEY in your environment or .env file."
        )

    return app


# Example values from .env.example and its older versions: anyone can read them.
_PLACEHOLDERS = {"change-me", "change-me-too", "changeme", "secret", "choose-a-strong-password", "password"}
MIN_SECRET_KEY_LENGTH = 32


def _refuse_known_secrets(app):
    """Stop at start-up instead of running with a key or password others know.

    With a known SECRET_KEY anyone can sign a session cookie for any account.
    Tests use a short fixed key, so the key is checked only outside testing.
    """
    from .auth import MIN_PASSWORD_LENGTH

    key = app.config.get("SECRET_KEY") or ""
    if not app.testing and not app.config.get("SECRET_KEY_IS_GENERATED") and (
            key.lower() in _PLACEHOLDERS or len(key) < MIN_SECRET_KEY_LENGTH):
        raise RuntimeError(
            f"SECRET_KEY is an example value or shorter than {MIN_SECRET_KEY_LENGTH} characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"')

    password = app.config.get("ADMIN_PASSWORD")
    if password and (password.lower() in _PLACEHOLDERS or len(password) < MIN_PASSWORD_LENGTH):
        raise RuntimeError(
            f"ADMIN_PASSWORD is an example value or shorter than {MIN_PASSWORD_LENGTH} characters. "
            "Choose another one, or leave it empty once the first administrator exists.")


@login_manager.user_loader
def load_user(user_id):
    # "<id>:<session version>" (User.get_id). Cookies holding another version, or the
    # bare id stored before versions existed, no longer sign anyone in.
    uid, _, version = user_id.partition(":")
    if not uid.isdigit() or not version.isdigit():
        return None
    user = db.session.get(User, int(uid))
    if not user or not user.active or user.session_version != int(version):
        return None  # a removed person is signed out at once
    return user


# Columns added after the first release. ``db.create_all()`` creates missing
# tables but never changes existing ones, so older databases get these columns
# here. Each entry: (table, column, SQL type and default).
_ADDED_COLUMNS = [
    ("project_image", "media_type", "VARCHAR(10) NOT NULL DEFAULT 'image'"),
    # Projects created before this setting existed keep handing out whole items.
    ("project", "hand_out_per_item", "BOOLEAN NOT NULL DEFAULT 1"),
    ("user", "active", "BOOLEAN NOT NULL DEFAULT 1"),
    # user.failed_logins and user.locked_until are no longer used (see LoginFailure); old databases keep them.
    ("user", "session_version", "INTEGER NOT NULL DEFAULT 0"),
    ("user", "must_change_password", "BOOLEAN NOT NULL DEFAULT 0"),
    ("question_answer", "cant_tell", "BOOLEAN NOT NULL DEFAULT 0"),
    ("login_failure", "email", "VARCHAR(255)"),
]


# Columns made longer after the first release: (column of the user table, characters).
_WIDENED_COLUMNS = [("email", 255), ("full_name", 100)]


def _upgrade_schema(app):
    """Bring older databases up to date: missing columns, longer name and email
    columns, and the one-task-per-item-and-question rule. Safe to run every start."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    for table, column, definition in _ADDED_COLUMNS:
        if table not in tables:
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column in existing:
            continue
        with db.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}"))  # quoted: `user` is a keyword
        app.logger.info("Database upgraded: added %s.%s", table, column)

    if "user" in tables and db.engine.dialect.name == "mysql":  # SQLite doesn't enforce lengths
        for column, length in _WIDENED_COLUMNS:
            current = next(c for c in inspector.get_columns("user") if c["name"] == column)
            if getattr(current["type"], "length", length) < length:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE `user` MODIFY `{column}` VARCHAR({length}) NOT NULL"))
                app.logger.info("Database upgraded: user.%s holds %d characters", column, length)

    if "annotation_task" in tables:
        rule = "unique_task_item_question"
        names = {c["name"] for c in inspector.get_unique_constraints("annotation_task")}
        names |= {i["name"] for i in inspector.get_indexes("annotation_task")}
        if rule not in names:
            with db.engine.begin() as conn:
                duplicate = conn.execute(text(
                    "SELECT 1 FROM annotation_task GROUP BY project_id, image_id, question_id "
                    "HAVING COUNT(*) > 1 LIMIT 1")).first()
                if duplicate:  # never delete data to make the rule fit; say so instead
                    app.logger.warning("Duplicate tasks found; the one-task-per-item-and-question rule was not added")
                else:
                    conn.execute(text(f"CREATE UNIQUE INDEX {rule} ON annotation_task "
                                      "(project_id, image_id, question_id)"))
                    app.logger.info("Database upgraded: one task per item and question")


# The graded version (tag v1.0-thesis-submitted) created this administrator on every start, with a
# password written in its source. That source is public, so the password is known to anyone.
_V1_DEFAULT_ADMIN = ("admin@email.com", "123")


def _retire_v1_default_admin(app):
    """A database first made by the graded version may still hold its default administrator.

    _upgrade_schema keeps that account working, so if it still has the published password, the
    password is replaced with a random one nobody knows and its sessions end. The account itself
    stays, so its owner can set a new password with scripts/admin_manager.py.
    """
    import secrets

    email, password = _V1_DEFAULT_ADMIN
    user = User.query.filter_by(email=email).first()
    if user and user.check_password(password):
        user.set_password(secrets.token_urlsafe(32))  # also ends every session
        db.session.commit()
        app.logger.warning("%s still had the published default password from the graded version; it was "
                           "replaced with a random one. Set a new one with: python scripts/admin_manager.py", email)


def _ensure_admin(app):
    """Create the first administrator from ADMIN_EMAIL / ADMIN_PASSWORD.

    No account with a default password is ever created. If no admin exists
    and the variables are not set, a warning explains how to create one.
    """
    if User.query.filter_by(role="admin").first():
        return

    email = app.config.get("ADMIN_EMAIL")
    password = app.config.get("ADMIN_PASSWORD")
    if email and password:
        User().register_admin(app.config.get("ADMIN_NAME", "Administrator"), email, password)
        app.logger.info("Created initial administrator %s", email)
    elif not app.testing:
        app.logger.warning(
            "No administrator account exists. Set ADMIN_EMAIL and ADMIN_PASSWORD "
            "and restart, or run: python scripts/admin_manager.py"
        )


# Pages that take files. Only these, and only for admins and managers, accept up to
# MAX_UPLOAD_LENGTH; anywhere else a visitor could make the server receive and parse
# hundreds of MB (CSRF reads the form before any role check).
UPLOAD_ENDPOINTS = {"admin.create_project", "admin.import_users", "manager.edit_project_questions",
                    "annotation_admin.upload_images"}


def _allow_big_uploads(app):
    @app.before_request
    def _raise_the_limit_on_upload_pages():
        if (request.endpoint in UPLOAD_ENDPOINTS and current_user.is_authenticated
                and current_user.role in ("admin", "project-admin")):
            request.max_content_length = app.config["MAX_UPLOAD_LENGTH"]


def _configure_https(app):
    """Cookies over HTTPS only, and the visitor's address and https from a trusted proxy's headers."""
    if app.config.get("SESSION_COOKIE_SECURE") is None:  # not set: HTTPS only, except on the debug server
        app.config["SESSION_COOKIE_SECURE"] = not app.debug
    app.config["REMEMBER_COOKIE_SECURE"] = app.config["SESSION_COOKIE_SECURE"]
    proxies = app.config.get("TRUSTED_PROXIES") or 0
    if proxies:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)


# What a log viewer may show as a line break, or use to reorder text on screen: control characters
# (\n and \r, but also \v, \f, \x1c-\x1e and \x85, which Python's splitlines() and some editors break
# at), the Unicode line and paragraph separators, and the text-direction controls. Other invisible
# characters, such as the joiners in Persian names and in emoji, are left as they are.
_UNSAFE_IN_LOGS = re.compile("[\x00-\x1f\x7f-\x9f  ؜‎‏‪-‮⁦-⁩]")
_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape_for_log(match):
    ch = match.group()
    return _ESCAPES.get(ch) or (f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}")


class _OneLinePerEvent(logging.Filter):
    """Write line breaks, and other characters that could disguise a line, as escapes (\\n, \\x0b ...).

    Some logged values come from visitors (the address they asked for, say), and a
    line break in one would let them add a made-up line such as a LOGIN_OK.
    """

    def filter(self, record):
        message = record.getMessage()
        if _UNSAFE_IN_LOGS.search(message):
            record.msg, record.args = _UNSAFE_IN_LOGS.sub(_escape_for_log, message), None
        return True


class SharedRotatingFileHandler(RotatingFileHandler):
    """A RotatingFileHandler that several processes, the Gunicorn workers, can share.

    Each worker opens its own handler on the same file. A plain RotatingFileHandler then rotates
    whenever its own worker thinks the file is full and keeps writing to a file another worker has
    already renamed, so files are rotated more often than needed and the oldest ones, with the
    history in them, are deleted early. Here each write holds a lock on a side file, reopens the
    log if another worker rotated it, and measures the file as it is on disk.
    """

    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)
        self._lock_file = open(self.baseFilename + ".lock", "a")

    def emit(self, record):
        try:
            if fcntl:
                fcntl.flock(self._lock_file, fcntl.LOCK_EX)
            try:
                if self.stream is not None and self._renamed_by_another_process():
                    self.stream.close()
                    self.stream = None
                if self.stream is None:
                    self.stream = self._open()
                self.stream.seek(0, os.SEEK_END)  # the size other workers' lines gave it, too
                super().emit(record)  # rotates if needed, writes and flushes
            finally:
                if fcntl:
                    fcntl.flock(self._lock_file, fcntl.LOCK_UN)
        except Exception:
            self.handleError(record)

    def _renamed_by_another_process(self):
        try:
            on_disk = os.stat(self.baseFilename)
        except FileNotFoundError:
            return True
        mine = os.fstat(self.stream.fileno())
        return (on_disk.st_dev, on_disk.st_ino) != (mine.st_dev, mine.st_ino)

    def close(self):
        super().close()
        self._lock_file.close()


def _configure_logging(app):
    level = getattr(logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    app.logger.setLevel(level)
    for logger in (app.logger, logging.getLogger("audit"), logging.getLogger("refused")):
        if not any(isinstance(f, _OneLinePerEvent) for f in logger.filters):
            logger.addFilter(_OneLinePerEvent())

    log_dir = app.config.get("LOG_DIR")
    if not log_dir or app.testing:
        return

    os.makedirs(log_dir, exist_ok=True)
    handler = SharedRotatingFileHandler(
        os.path.join(log_dir, "app.log"), maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    handler.setLevel(level)
    app.logger.addHandler(handler)

    # Separate audit trail for security-relevant events (sign-ins, account and project changes,
    # exports), and a file of its own for refused requests: anyone can cause those by the thousand,
    # so in one file they could push the sign-in history out of the size-limited log.
    for name, filename in (("audit", "audit.log"), ("refused", "refused.log")):
        trail = logging.getLogger(name)
        trail.setLevel(logging.INFO)
        if not trail.handlers:
            trail_handler = SharedRotatingFileHandler(
                os.path.join(log_dir, filename), maxBytes=1_000_000, backupCount=10, encoding="utf-8"
            )
            trail_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            trail.addHandler(trail_handler)


MAX_LOGGED_PATH = 200


def _register_error_handlers(app):
    refused = logging.getLogger("refused")  # refused.log, not audit.log: see _configure_logging

    def _who():
        # The address is the visitor's choice: a long one is cut, so a few thousand requests can't push
        # older events out of the size-limited log. Its full length is still recorded.
        path = request.path
        if len(path) > MAX_LOGGED_PATH:
            path = f"{path[:MAX_LOGGED_PATH]}... ({len(path)} characters)"
        return f"user_id={getattr(current_user, 'id', None)} ip={request.remote_addr} path={path}"

    @app.errorhandler(403)
    def forbidden(error):
        refused.warning("ACCESS_DENIED %s", _who())
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def too_large(error):
        return render_template("errors/413.html"), 413

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template("errors/500.html"), 500

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        refused.warning("CSRF_REJECTED %s", _who())
        if request.endpoint == "auth.logout":  # an expired page's Sign out: nothing was done, just go to sign-in
            return redirect(url_for("auth.login"))
        return render_template("errors/400.html", reason=error.description), 400
