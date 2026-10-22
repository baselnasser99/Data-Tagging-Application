"""Application configuration.

All secrets and environment-specific values are read from environment
variables (optionally loaded from a local ``.env`` file). Nothing sensitive
is hard-coded. See ``.env.example`` for the full list.
"""
import os
import secrets
from datetime import timedelta


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # Name shown in the browser tab, header and sign-in page.
    APP_NAME = os.environ.get("APP_NAME", "Data Tagging")

    # A random key is generated when SECRET_KEY is not set. That is safe, but
    # every restart logs everyone out, so production must set SECRET_KEY.
    # Example values and keys under 32 characters are refused (see create_app).
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SECRET_KEY_IS_GENERATED = not os.environ.get("SECRET_KEY")  # unset or empty

    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///database.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Session cookie hardening (thesis section 6.5.1)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # None (not set) means HTTPS only, except on the debug server (create_app decides).
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE") if os.environ.get("SESSION_COOKIE_SECURE") else None
    REMEMBER_COOKIE_HTTPONLY = True
    # Idle timeout: the session expires after this many minutes without a request.
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.environ.get("SESSION_IDLE_MINUTES", "60"))
    )
    SESSION_REFRESH_EACH_REQUEST = True

    # CSRF protection via Flask-WTF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None  # token valid for the whole session

    # Request size. Every request, signing in included, may send at most MAX_REQUEST_MB; only
    # the upload pages, for admins and managers, accept up to MAX_UPLOAD_MB (see create_app).
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_REQUEST_MB", "2")) * 1024 * 1024
    MAX_UPLOAD_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "512")) * 1024 * 1024

    # Optional first administrator, created on startup if no admin exists yet.
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
    ADMIN_NAME = os.environ.get("ADMIN_NAME", "Administrator")

    # Reverse proxies in front of the app whose X-Forwarded-For/-Proto headers are believed.
    # 0 when browsers connect directly: anyone could send those headers.
    TRUSTED_PROXIES = int(os.environ.get("TRUSTED_PROXIES", "0"))

    LOG_DIR = os.environ.get("LOG_DIR", "logs")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SECRET_KEY_IS_GENERATED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    ADMIN_EMAIL = None
    ADMIN_PASSWORD = None
    LOG_DIR = None
