import logging
import os
from logging.handlers import RotatingFileHandler

import pymysql
from flask import Flask, render_template, request
from flask_wtf.csrf import CSRFError

pymysql.install_as_MySQLdb()

try:  # Load a local .env file when python-dotenv is installed.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - optional dependency
    pass

from .config import Config  # noqa: E402  (must come after load_dotenv)
from .extns import csrf, db, login_manager  # noqa: E402
from .models import User  # noqa: E402


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(config_object or Config)

    _configure_logging(app)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .admin import admin
    from .auth import auth
    from .annotation import annotation
    from .groups import groups
    from .manager import manager
    from .annotation_admin import annotation_admin
    from .jinja_utils import getattr_safe, plural, sidebar_summary

    app.register_blueprint(auth)
    app.register_blueprint(admin)
    app.register_blueprint(groups)
    app.register_blueprint(manager)
    app.register_blueprint(annotation_admin)
    app.register_blueprint(annotation)

    app.jinja_env.globals["getattr_safe"] = getattr_safe
    app.jinja_env.globals["plural"] = plural
    app.jinja_env.globals["sidebar_summary"] = sidebar_summary
    app.jinja_env.globals["app_name"] = app.config["APP_NAME"]

    _register_error_handlers(app)

    @app.after_request
    def _no_store(response):
        # Pages show private data: don't let the browser keep a copy that the
        # Back button could show after sign-out. Static files may be cached.
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    with app.app_context():
        db.create_all()
        _upgrade_schema(app)
        _ensure_admin(app)

    if app.config.get("SECRET_KEY_IS_GENERATED") and not app.testing:
        app.logger.warning(
            "SECRET_KEY is not set; using a random key. Users will be logged out "
            "on every restart. Set SECRET_KEY in your environment or .env file."
        )

    return app


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# Columns added after the first release. ``db.create_all()`` creates missing
# tables but never changes existing ones, so older databases get these columns
# here. Each entry: (table, column, SQL type and default).
_ADDED_COLUMNS = [
    ("project_image", "media_type", "VARCHAR(10) NOT NULL DEFAULT 'image'"),
    # Projects created before this setting existed keep handing out whole items.
    ("project", "hand_out_per_item", "BOOLEAN NOT NULL DEFAULT 1"),
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
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
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


def _configure_logging(app):
    level = getattr(logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    app.logger.setLevel(level)

    log_dir = app.config.get("LOG_DIR")
    if not log_dir or app.testing:
        return

    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"), maxBytes=1_000_000, backupCount=5
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    handler.setLevel(level)
    app.logger.addHandler(handler)

    # Separate audit trail for security-relevant events (sign-ins etc.)
    audit = logging.getLogger("audit")
    audit.setLevel(logging.INFO)
    if not audit.handlers:
        audit_handler = RotatingFileHandler(
            os.path.join(log_dir, "audit.log"), maxBytes=1_000_000, backupCount=10
        )
        audit_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        audit.addHandler(audit_handler)


def _register_error_handlers(app):
    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template("errors/500.html"), 500

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return render_template("errors/400.html", reason=error.description), 400
