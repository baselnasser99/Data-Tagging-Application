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

    def _reset_request_cache():
        # One app context stays open, so its g would otherwise carry the signed-in user and the
        # CSRF token from one request into the next (see tests/conftest.py).
        from flask import g

        g.pop("_login_user", None)
        g.pop("csrf_token", None)

    app.before_request_funcs.setdefault(None, []).insert(0, _reset_request_cache)
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


def test_sign_out_needs_the_page_token(csrf_app):
    # A plain link from another site used to sign the person out on every device.
    from application.extns import db

    member = User(full_name="M", email="out@example.com", role="end-user")
    member.set_password("password123")
    db.session.add(member)
    db.session.commit()
    client = csrf_app.test_client()
    client.post("/login", data={"email": member.email, "password": "password123",
                                "csrf_token": _token(client.get("/login").data)})
    version = member.session_version

    assert client.get("/logout").status_code == 405
    resp = client.post("/logout")  # no token: what a cross-site form would send
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")
    db.session.refresh(member)
    assert member.session_version == version

    page = client.get("/user/dashboard").data
    assert b'action="/logout"' in page
    client.post("/logout", data={"csrf_token": _token(page)})
    db.session.refresh(member)
    assert member.session_version != version


def test_every_post_form_in_templates_has_csrf_token():
    import glob

    for path in glob.glob("application/templates/**/*.html", recursive=True):
        with open(path, encoding="utf-8") as f:
            html = f.read()
        forms = re.findall(r"<form\b[^>]*method=[\"']post[\"'][^>]*>", html, re.I)
        assert html.count("csrf_token()") >= len(forms), path


def test_the_graded_versions_default_admin_password_stops_working(app, db):
    # A database first made by v1.0-thesis-submitted, upgraded in place: its default admin, whose
    # password is published in that tag, used to sign in as before.
    from application import _V1_DEFAULT_ADMIN, _retire_v1_default_admin
    from tests.conftest import login

    email, published = _V1_DEFAULT_ADMIN
    old_admin = User()
    old_admin.register_admin("Administrator", email, published)
    kept = User()
    kept.register_admin("Other", "kept@example.com", published)  # same password, another email: not this account
    signed_in = app.test_client()
    login(signed_in, email, published)

    _retire_v1_default_admin(app)

    assert not old_admin.check_password(published) and old_admin.active
    assert signed_in.get("/admin/dashboard").status_code == 302  # its sessions ended
    resp = login(app.test_client(), email, published)
    assert resp.headers["Location"].endswith("/login")
    assert kept.check_password(published)
    old_admin.set_password("a-new-long-password")  # what scripts/admin_manager.py does
    assert old_admin.check_password("a-new-long-password")


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


def test_refused_requests_go_to_their_own_log_file(tmp_path):
    # Anyone can cause refused requests by the thousand; in audit.log they could push the
    # sign-in history out of the size-limited file.
    import logging

    class Config(TestingConfig):
        TESTING = False
        WTF_CSRF_ENABLED = True
        SECRET_KEY = "k" * 48
        LOG_DIR = str(tmp_path)

    app = create_app(Config)
    app.test_client_class = BufferedClient
    trails = [logging.getLogger(name) for name in ("audit", "refused")]
    try:
        with app.app_context():
            client = app.test_client()
            for _ in range(3):
                client.post("/user/group-project/1-1")  # no CSRF token
            client.post("/login", data={"email": "nobody@example.com", "password": "x",
                                        "csrf_token": _token(client.get("/login").data)})
            for trail in trails:
                for handler in trail.handlers:
                    handler.flush()
            audit = (tmp_path / "audit.log").read_text(encoding="utf-8")
            refused = (tmp_path / "refused.log").read_text(encoding="utf-8")
            assert "LOGIN_FAILED" in audit and "CSRF_REJECTED" not in audit
            assert refused.count("CSRF_REJECTED") == 3 and "LOGIN_FAILED" not in refused
            _close_database()
    finally:
        for trail in trails:  # the loggers are shared with the other tests
            for handler in list(trail.handlers):
                trail.removeHandler(handler)
                handler.close()


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


def test_app_container_gets_only_the_settings_it_reads():
    # env_file handed the app MYSQL_ROOT_PASSWORD too, which it never uses.
    with open("docker-compose.yml", encoding="utf-8") as f:
        web = f.read().split("\n  web:")[1].split("\nvolumes:")[0]
    assert "env_file:" not in web and "MYSQL_ROOT_PASSWORD" not in web
    assert "SECRET_KEY:" in web and "ADMIN_PASSWORD:" in web


def test_docker_image_leaves_out_local_env_files_and_example_accounts():
    with open(".dockerignore", encoding="utf-8") as f:
        ignored = {line.strip().rstrip("/") for line in f if line.strip() and not line.startswith("#")}
    assert {".env", ".env.*", ".claude", ".idea", ".vscode", "scripts/sample_users.csv"} <= ignored


def test_workflows_get_a_read_only_token():
    import glob

    for path in glob.glob(".github/workflows/*.yml"):
        with open(path, encoding="utf-8") as f:
            workflow = f.read()
        assert "permissions:\n  contents: read" in workflow, path
        assert "persist-credentials: false" in workflow, path


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
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";"))
    assert directives["script-src"] == "'self'"  # no inline scripts, no other sites


@pytest.mark.parametrize("path", ["/login", "/admin/dashboard", "/no-such-page"])
def test_csp_allows_nothing_from_other_sites(admin_client, path):
    # Stylesheets and fonts used to come from Google Fonts and a CDN; they are kept in static/vendor now.
    csp = admin_client.get(path).headers["Content-Security-Policy"]
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";"))
    assert directives["default-src"] == "'self'"
    assert directives["style-src"] == "'self' 'unsafe-inline'"  # inline style="" attributes only
    assert directives["font-src"] == "'self'" and directives["img-src"] == "'self' data:"
    assert directives["object-src"] == "'none'" and directives["frame-ancestors"] == "'none'"


def test_pages_and_stylesheets_link_nothing_from_other_sites():
    import glob

    external = re.compile(r"""(?:href|src)=["']https?://|@import\s+url\(\s*["']?https?://|url\(\s*["']?https?://""")
    files = glob.glob("application/templates/**/*.html", recursive=True) + glob.glob("application/static/**/*.css",
                                                                                       recursive=True)
    offenders = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            if external.search(f.read()):
                offenders.append(path)
    assert offenders == []


def test_bundled_fonts_are_served(client):
    for path in ("/static/vendor/atkinson-hyperlegible/fonts.css",
                 "/static/vendor/atkinson-hyperlegible/atkinson-400-latin.woff2",
                 "/static/vendor/atkinson-hyperlegible/atkinson-700-latin-ext.woff2",
                 "/static/vendor/font-awesome/css/font-awesome.min.css",
                 "/static/vendor/font-awesome/fonts/fontawesome-webfont.woff2"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        resp.close()


# Oldest versions without published vulnerabilities: jQuery 3.5.0 fixed CVE-2020-11022/-11023 (and
# CVE-2019-11358, CVE-2015-9251 before it); Bootstrap 3.4.1 fixed CVE-2018-14040/-14041/-14042, CVE-2019-8331.
SAFE_VERSIONS = {"jQuery": (3, 5, 0), "Bootstrap": (3, 4, 1)}


def test_bundled_javascript_libraries_are_not_known_to_be_vulnerable():
    import glob

    found = {}
    for path in glob.glob("application/static/**/*.js", recursive=True):
        path = path.replace("\\", "/")
        with open(path, encoding="utf-8", errors="replace") as f:
            header = f.read(400)
        match = re.search(r"\b(jQuery|Bootstrap) v(\d+)\.(\d+)\.(\d+)", header)
        if match:
            name, version = match.group(1), tuple(int(n) for n in match.group(2, 3, 4))
            found[path] = version
            assert version >= SAFE_VERSIONS[name], f"{path}: {name} {'.'.join(map(str, version))}"
    assert len(found) == 2, found  # one jQuery and one Bootstrap, no forgotten old copies

    with open("application/templates/base.html", encoding="utf-8") as f:
        base = f.read()
    for path in found:  # and those are the files the pages load
        assert path.removeprefix("application/static/") in base, path


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


# ---- request size: big bodies only on the upload pages, for admins and managers ----

def _post_big_file(client, url, field="file", name="big.png", **fields):
    """POST a 3 MB file (over the 2 MB for ordinary requests), encoded in memory:
    the test client would otherwise spool it to a temporary file it never closes."""
    import io

    from werkzeug.datastructures import FileStorage
    from werkzeug.test import encode_multipart

    upload = FileStorage(io.BytesIO(b"\0" * 3 * 1024 * 1024), filename=name)
    boundary, body = encode_multipart({**fields, field: upload})
    return client.post(url, data=body, content_type=f"multipart/form-data; boundary={boundary}")


def test_sign_in_page_refuses_a_huge_body(client):
    # Before signing in, nobody should be able to make the server receive and parse hundreds of MB.
    resp = _post_big_file(client, "/login", email="a@example.com", password="x")
    assert resp.status_code == 413
    assert b"too large" in resp.data


def test_member_pages_refuse_a_huge_body(user_client):
    assert _post_big_file(user_client, "/user/change-password").status_code == 413


def test_big_uploads_work_with_csrf_switched_on(tmp_path):
    # CSRF reads the whole form before the view: the larger limit must be set before that.
    from application.models import Project, ProjectImage

    class Config(TestingConfig):
        WTF_CSRF_ENABLED = True
        UPLOAD_ROOT = str(tmp_path)

    app = create_app(Config)
    app.test_client_class = BufferedClient
    with app.app_context():
        admin = User(full_name="A", email="big@example.com", role="admin")
        admin.set_password("password123")
        project = Project(name="Big", desc="d", is_annotation_project=True)
        _db.session.add_all([admin, project])
        _db.session.commit()
        url = f"/admin/upload-images/{project.id}"
    # Requests outside that context, so each gets a fresh one: Flask-WTF caches the token in g.
    client = app.test_client()
    client.post("/login", data={"email": "big@example.com", "password": "password123",
                                "csrf_token": _token(client.get("/login").data)})
    resp = _post_big_file(client, url, field="image_zip", csrf_token=_token(client.get(url).data))
    with app.app_context():
        try:
            assert resp.status_code == 302
            assert ProjectImage.query.count() == 1
        finally:
            _close_database()


def test_upload_pages_take_big_files_only_from_admins_and_managers(app, make_user, admin_user, end_user):
    # In production CSRF reads the form first, so the limit must already be the right one by then.
    from flask import request
    from flask_login import login_user

    manager = make_user(role="project-admin")
    expected = [(end_user, app.config["MAX_CONTENT_LENGTH"]), (manager, app.config["MAX_UPLOAD_LENGTH"]),
                (admin_user, app.config["MAX_UPLOAD_LENGTH"])]
    for user, limit in expected:
        with app.test_request_context("/admin/import-users", method="POST"):
            login_user(user)
            app.preprocess_request()
            assert request.max_content_length == limit, user.role


def test_admins_and_managers_can_still_upload_big_files(app, db, admin_client, make_project_admin):
    from application.models import Project, ProjectImage
    from tests.conftest import login

    project = Project(name="Big", desc="d", is_annotation_project=True)
    db.session.add(project)
    db.session.commit()
    assert _post_big_file(admin_client, f"/admin/upload-images/{project.id}", field="image_zip").status_code == 302
    manager = app.test_client()
    login(manager, make_project_admin(project).email)
    _post_big_file(manager, f"/admin/upload-images/{project.id}", field="image_zip", name="second.png")
    assert ProjectImage.query.filter_by(project_id=project.id).count() == 2
