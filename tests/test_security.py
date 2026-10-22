import re
import secrets

import pytest

from application import create_app
from application.config import TestingConfig
from application.extns import db as _db
from application.models import User
from tests.conftest import BufferedClient


def _close_database():
    _db.session.remove()
    _db.drop_all()
    _db.engine.dispose()


@pytest.fixture
def csrf_app(tmp_path):
    class Config(TestingConfig):
        WTF_CSRF_ENABLED = True

    app = create_app(Config)
    app.test_client_class = BufferedClient
    with app.app_context():
        yield app
        _close_database()


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
        with open(path, encoding="utf-8") as f:
            html = f.read()
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
        try:
            admin = User.query.filter_by(email="owner@example.com").one()
            assert admin.role == "admin"
            assert admin.check_password("a-long-password")
        finally:
            _close_database()


class _Production(TestingConfig):
    TESTING = False  # the SECRET_KEY check is skipped in tests, which use a short fixed key


@pytest.mark.parametrize("key", ["change-me", "CHANGE-ME", "short-but-not-a-placeholder"])
def test_placeholder_or_short_secret_key_stops_start_up(key):
    # Anyone who knows the key can sign a session cookie for any account.
    class Config(_Production):
        SECRET_KEY = key

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(Config)


def test_long_random_secret_key_is_accepted():
    class Config(_Production):
        SECRET_KEY = secrets.token_hex(32)

    app = create_app(Config)
    with app.app_context():
        _close_database()


@pytest.mark.parametrize("password", ["choose-a-strong-password", "short"])
def test_placeholder_or_short_admin_password_stops_start_up(password):
    class Config(TestingConfig):
        ADMIN_EMAIL = "owner@example.com"
        ADMIN_PASSWORD = password

    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        create_app(Config)


def test_env_example_ships_no_secrets():
    with open(".env.example", encoding="utf-8") as f:
        values = dict(line.strip().split("=", 1) for line in f if "=" in line and not line.startswith("#"))
    for name in ("SECRET_KEY", "ADMIN_PASSWORD", "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD"):
        assert values[name] == "", f"{name} must be left for the operator to fill in"


def test_session_cookie_is_http_only_and_same_site(client, admin_user):
    resp = client.post("/login", data={"email": admin_user.email, "password": "password123"})
    cookie = resp.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Expires=" in cookie  # permanent session -> idle timeout applies


def _settings(config):
    app = create_app(config)
    with app.app_context():
        _close_database()
    return app.config


@pytest.mark.parametrize("debug, secure", [(False, True), (True, False)])
def test_cookies_are_secure_unless_on_the_debug_server(debug, secure):
    # Cookies sent over plain http can be read by anyone on the network.
    class Config(TestingConfig):
        SESSION_COOKIE_SECURE = None  # SESSION_COOKIE_SECURE not set in the environment
        DEBUG = debug

    settings = _settings(Config)
    assert settings["SESSION_COOKIE_SECURE"] is secure
    assert settings["REMEMBER_COOKIE_SECURE"] is secure


def test_secure_cookie_flag_can_still_be_switched_off():
    class Config(TestingConfig):
        SESSION_COOKIE_SECURE = False

    assert _settings(Config)["SESSION_COOKIE_SECURE"] is False


def test_login_cookie_is_marked_secure():
    class Config(TestingConfig):
        SESSION_COOKIE_SECURE = None

    app = create_app(Config)
    with app.app_context():
        try:
            admin = User(full_name="A", email="secure@example.com", role="admin")
            admin.set_password("password123")
            _db.session.add(admin)
            _db.session.commit()
            resp = app.test_client().post("/login", data={"email": "secure@example.com", "password": "password123"})
            assert "; Secure" in resp.headers.get("Set-Cookie", "")
        finally:
            _close_database()


def _audited_ip(app, caplog, **environ):
    caplog.set_level("INFO", logger="audit")
    with app.app_context():
        try:
            app.test_client().post("/login", data={"email": "nobody@example.com", "password": "x"}, **environ)
        finally:
            _close_database()
    return [r.getMessage() for r in caplog.records if "LOGIN_FAILED" in r.getMessage()][0]


def test_trusted_proxy_gives_the_visitors_address(caplog):
    class Config(TestingConfig):
        TRUSTED_PROXIES = 1

    line = _audited_ip(create_app(Config), caplog, headers={"X-Forwarded-For": "203.0.113.9"},
                       environ_base={"REMOTE_ADDR": "172.18.0.1"})
    assert "ip=203.0.113.9" in line


def test_forwarded_headers_are_ignored_without_a_trusted_proxy(caplog):
    line = _audited_ip(create_app(TestingConfig), caplog, headers={"X-Forwarded-For": "203.0.113.9"},
                       environ_base={"REMOTE_ADDR": "198.51.100.7"})
    assert "ip=198.51.100.7" in line  # a visitor can't pick the address the audit log records


def test_docker_compose_publishes_the_app_on_localhost_only():
    # Browsers should reach Gunicorn only through an HTTPS reverse proxy on the same machine.
    with open("docker-compose.yml", encoding="utf-8") as f:
        compose = f.read()
    assert '"127.0.0.1:5000:5000"' in compose
    assert '"5000:5000"' not in compose


def test_docker_image_leaves_out_history_tests_and_local_files():
    # The git history holds the graded version's fixed SECRET_KEY and admin password (SECURITY.md);
    # .venv is a local (often Windows) environment; the rest isn't needed to run the app.
    with open(".dockerignore", encoding="utf-8") as f:
        ignored = {line.strip().rstrip("/") for line in f if line.strip() and not line.startswith("#")}
    wanted = {".git", ".github", ".venv", "tests", "docs", "logs", "instance", "application/static/uploads",
              ".coverage", "coverage.xml", "htmlcov", ".pytest_cache"}
    assert wanted <= ignored, sorted(wanted - ignored)


def test_database_container_gets_only_its_own_settings():
    # env_file would hand it SECRET_KEY and ADMIN_PASSWORD too; it needs only the MYSQL_* values.
    with open("docker-compose.yml", encoding="utf-8") as f:
        database = f.read().split("\n  web:")[0]
    assert "env_file:" not in database
    assert "MYSQL_ROOT_PASSWORD:" in database


@pytest.mark.parametrize("path", ["/admin/delete-project/1", "/admin/delete-group/1"])
def test_destructive_actions_do_not_accept_get(admin_client, path):
    assert admin_client.get(path).status_code == 405


def test_debug_route_removed(client):
    assert client.get("/test-admin").status_code == 404


def test_custom_404_page(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert b"Page not found" in resp.data
    assert b"/does-not-exist" in resp.data  # the page shows the address that was asked for


def test_404_page_escapes_the_address(client):
    resp = client.get("/<script>alert(1)</script>")
    assert resp.status_code == 404
    assert b"<script>alert" not in resp.data
    assert b"&lt;script&gt;" in resp.data


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
        with open(path, encoding="utf-8") as f:
            html = f.read()
        for tag in re.findall(r"<script\b[^>]*>", html, re.I):
            assert "src=" in tag, f"inline <script> in {path}"
        assert not re.search(r"<[^>]*\son[a-z]+\s*=", html, re.I), f"inline event handler in {path}"
        assert "javascript:" not in html.lower(), path


def test_uploaded_files_need_sign_in(app, client, admin_user):
    import os

    from application.uploads import upload_dir
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
