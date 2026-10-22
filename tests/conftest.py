import io
import zipfile

import pytest
from flask.testing import FlaskClient

from application import create_app
from application.config import TestingConfig
from application.extns import db as _db
from application.models import ProjectAdmin, User


class BufferedClient(FlaskClient):
    """Reads each response in full and closes it, so served files are not left open."""

    def open(self, *args, buffered=True, **kwargs):
        return super().open(*args, buffered=buffered, **kwargs)


@pytest.fixture
def app(tmp_path):
    class Config(TestingConfig):
        UPLOAD_ROOT = str(tmp_path / "uploads")

    app = create_app(Config)
    app.test_client_class = BufferedClient

    # The tests keep one app context open so they can query the database
    # between requests. Flask reuses that context (and its ``g``) for every
    # request, so Flask-Login's per-request user cache must be cleared to keep
    # several logged-in test clients independent. It runs first, before the
    # app's own before_request hooks, since some of them read current_user.
    def _reset_login_cache():
        from flask import g

        g.pop("_login_user", None)

    app.before_request_funcs.setdefault(None, []).insert(0, _reset_login_cache)

    with app.app_context():
        yield app
        _db.session.remove()
        _db.drop_all()
        # Close the pooled SQLite connection; otherwise every test leaks one.
        _db.engine.dispose()


@pytest.fixture
def db(app):
    return _db


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_user(db):
    counter = {"n": 0}

    def _make(role="end-user", email=None, password="password123", name=None):
        counter["n"] += 1
        user = User(
            full_name=name or f"User {counter['n']}",
            email=email or f"user{counter['n']}@example.com",
            role=role,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    return _make


def login(client, email, password="password123", follow=False):
    return client.post(
        "/login", data={"email": email, "password": password}, follow_redirects=follow
    )


@pytest.fixture
def admin_user(make_user):
    return make_user(role="admin", email="admin@example.com", name="Admin")


@pytest.fixture
def admin_client(client, admin_user):
    login(client, admin_user.email)
    return client


@pytest.fixture
def end_user(make_user):
    return make_user(role="end-user", email="annotator@example.com", name="Annotator")


@pytest.fixture
def user_client(app, end_user):
    c = app.test_client()
    login(c, end_user.email)
    return c


@pytest.fixture
def make_project_admin(db, make_user, admin_user):
    def _make(project):
        manager = make_user(role="project-admin")
        db.session.add(
            ProjectAdmin(user_id=manager.id, project_id=project.id, assigned_by=admin_user.id)
        )
        db.session.commit()
        return manager

    return _make


def make_zip(files):
    """Build an in-memory ZIP. ``files`` maps archive names to bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


def flashes(client):
    with client.session_transaction() as sess:
        return [msg for _cat, msg in sess.get("_flashes", [])]
