import re

import pytest

from application import create_app
from application.config import TestingConfig
from application.models import User


@pytest.fixture
def csrf_app(tmp_path):
    class Config(TestingConfig):
        WTF_CSRF_ENABLED = True

    app = create_app(Config)
    with app.app_context():
        yield app


def _token(html):
    match = re.search(rb'name="csrf_token" value="([^"]+)"', html)
    assert match, "login form must include a CSRF token"
    return match.group(1).decode()


def test_post_without_csrf_token_is_rejected(csrf_app):
    client = csrf_app.test_client()
    resp = client.post("/login", data={"email": "a@b.c", "password": "x"})
    assert resp.status_code == 400
    assert b"Request rejected" in resp.data


def test_post_with_csrf_token_is_accepted(csrf_app):
    admin = User(full_name="A", email="csrf@example.com", role="admin")
    admin.set_password("password123")
    from application.extns import db

    db.session.add(admin)
    db.session.commit()

    client = csrf_app.test_client()
    token = _token(client.get("/login").data)
    resp = client.post(
        "/login", data={"email": "csrf@example.com", "password": "password123", "csrf_token": token}
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/dashboard")


def test_every_post_form_in_templates_has_csrf_token():
    import glob

    for path in glob.glob("application/templates/**/*.html", recursive=True):
        html = open(path, encoding="utf-8").read()
        forms = re.findall(r"<form\b[^>]*method=[\"']post[\"'][^>]*>", html, re.I)
        assert html.count("csrf_token()") >= len(forms), path


def test_no_default_admin_account_is_created(app):
    assert User.query.filter_by(role="admin").count() == 0
    assert User.query.filter_by(email="admin@email.com").first() is None


def test_initial_admin_comes_from_configuration(tmp_path):
    class Config(TestingConfig):
        ADMIN_EMAIL = "owner@example.com"
        ADMIN_PASSWORD = "a-long-password"

    app = create_app(Config)
    with app.app_context():
        admin = User.query.filter_by(email="owner@example.com").one()
        assert admin.role == "admin"
        assert admin.check_password("a-long-password")


def test_session_cookie_is_http_only_and_same_site(client, admin_user):
    resp = client.post("/login", data={"email": admin_user.email, "password": "password123"})
    cookie = resp.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Expires=" in cookie  # permanent session -> idle timeout applies


@pytest.mark.parametrize("path", ["/admin/delete-project/1", "/admin/delete-group/1"])
def test_destructive_actions_do_not_accept_get(admin_client, path):
    assert admin_client.get(path).status_code == 405


def test_debug_route_removed(client):
    assert client.get("/test-admin").status_code == 404


def test_custom_404_page(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert b"Page not found" in resp.data


def test_custom_403_page(user_client):
    resp = user_client.get("/admin/dashboard")
    assert resp.status_code == 403
    assert b"Access denied" in resp.data


def test_flash_messages_are_shown_once(admin_client):
    resp = admin_client.post("/admin/create-group", data={"group_name": "Once"}, follow_redirects=True)
    assert resp.data.count(b"Group created successfully") == 1


def test_every_page_sends_security_headers(client):
    resp = client.get("/login")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["/login", "/admin/dashboard"])
def test_csp_allows_only_our_own_scripts(admin_client, path):
    csp = admin_client.get(path).headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp


def test_templates_have_no_inline_scripts_or_event_handlers():
    """script-src 'self' would block them: all JavaScript lives in static files."""
    import glob

    for path in glob.glob("application/templates/**/*.html", recursive=True):
        html = open(path, encoding="utf-8").read()
        for tag in re.findall(r"<script\b[^>]*>", html, re.I):
            assert "src=" in tag, f"inline <script> in {path}"
        assert not re.search(r"<[^>]*\son[a-z]+\s*=", html, re.I), f"inline event handler in {path}"
        assert "javascript:" not in html.lower(), path


def test_uploaded_files_need_sign_in(app, client, admin_user):
    import os

    from application.admin import upload_dir
    from tests.conftest import login

    with open(os.path.join(upload_dir("images", "1"), "photo.png"), "wb") as f:
        f.write(b"png-bytes")
    assert client.get("/files/images/1/photo.png").status_code == 302  # to the sign-in page
    admin = app.test_client()
    login(admin, admin_user.email)
    assert admin.get("/files/images/1/photo.png").data == b"png-bytes"
    assert admin.get("/files/../config.py").status_code == 404

    # The old public address under /static/uploads/ no longer works.
    static_copy = os.path.join(app.static_folder, "uploads", "photo-test.png")
    os.makedirs(os.path.dirname(static_copy), exist_ok=True)
    with open(static_copy, "wb") as f:
        f.write(b"png-bytes")
    try:
        assert client.get("/static/uploads/photo-test.png").status_code == 404
        assert client.get("/static/css/app.css").status_code == 200
    finally:
        os.remove(static_copy)
