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
    # A random key is generated when SECRET_KEY is not set. That is safe, but
    # every restart logs everyone out, so production must set SECRET_KEY.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SECRET_KEY_IS_GENERATED = "SECRET_KEY" not in os.environ

    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///database.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Session cookie hardening (thesis section 6.5.1)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)  # True behind HTTPS
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    # Idle timeout: the session expires after this many minutes without a request.
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.environ.get("SESSION_IDLE_MINUTES", "60"))
    )
    SESSION_REFRESH_EACH_REQUEST = True

    # CSRF protection via Flask-WTF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None  # token valid for the whole session

    # Uploads
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "512")) * 1024 * 1024

    # Optional first administrator, created on startup if no admin exists yet.
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
    ADMIN_NAME = os.environ.get("ADMIN_NAME", "Administrator")

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
