import logging
import os
from logging.handlers import RotatingFileHandler

import pymysql
from flask import Flask, render_template
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
    from .end_user import end_user
    from .jinja_utils import getattr_safe

    app.register_blueprint(auth)
    app.register_blueprint(admin)
    app.register_blueprint(end_user)

    app.jinja_env.globals["getattr_safe"] = getattr_safe

    _register_error_handlers(app)

    with app.app_context():
        db.create_all()
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
            "and restart, or run: python admin_manager.py"
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
