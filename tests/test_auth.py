import pytest

from tests.conftest import login


@pytest.mark.parametrize(
    "role, target",
    [("admin", "/admin/dashboard"), ("project-admin", "/admin/my-projects"), ("end-user", "/user/dashboard")],
)
def test_login_redirects_each_role_to_its_home(client, make_user, role, target):
    user = make_user(role=role)
    resp = login(client, user.email)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(target)


def test_login_with_wrong_password_is_rejected(client, end_user):
    resp = login(client, end_user.email, password="wrong", follow=True)
    assert b"incorrect" in resp.data
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


def test_login_with_unknown_email_is_rejected(client):
    resp = login(client, "nobody@example.com", follow=True)
    assert b"incorrect" in resp.data


def test_logged_in_user_visiting_login_is_sent_home(user_client):
    resp = user_client.get("/login")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/user/dashboard")


def test_logout_clears_session(user_client):
    resp = user_client.get("/logout", follow_redirects=True)
    assert b"Logged out" in resp.data
    with user_client.session_transaction() as sess:
        assert "_user_id" not in sess and "id" not in sess
    assert user_client.get("/user/dashboard").status_code == 302


def test_private_pages_are_not_kept_by_the_browser(user_client):
    """Regression: after sign-out, the Back button showed the old dashboard."""
    assert user_client.get("/user/dashboard").headers["Cache-Control"] == "no-store"
    assert "no-store" not in user_client.get("/static/css/app.css").headers.get("Cache-Control", "")


def test_register_creates_end_user_with_hashed_password(client, db):
    from application.models import User

    resp = client.post(
        "/register",
        data={"fullname": "New Person", "email": "new@example.com", "password": "secret123"},
    )
    assert resp.status_code == 302
    user = User.query.filter_by(email="new@example.com").one()
    assert user.role == "end-user"
    assert user.password_hash != "secret123"
    assert user.check_password("secret123")


def test_register_rejects_duplicate_email(client, end_user):
    resp = client.post(
        "/register",
        data={"fullname": "Dup", "email": end_user.email, "password": "secret123"},
        follow_redirects=True,
    )
    assert b"already exist" in resp.data


def test_register_rejects_short_password(client, db):
    from application.models import User

    resp = client.post(
        "/register",
        data={"fullname": "Short", "email": "short@example.com", "password": "123"},
        follow_redirects=True,
    )
    assert b"at least 6 characters" in resp.data
    assert User.query.filter_by(email="short@example.com").first() is None


@pytest.mark.parametrize("path", ["/admin/dashboard", "/admin/users", "/user/dashboard", "/user/my-projects"])
def test_protected_pages_redirect_anonymous_visitors_to_login(client, path):
    resp = client.get(path)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_annotator_cannot_open_admin_pages(user_client):
    assert user_client.get("/admin/dashboard").status_code == 403
    assert user_client.get("/admin/users").status_code == 403


def test_admin_cannot_open_annotator_pages(admin_client):
    assert admin_client.get("/user/my-projects").status_code == 403


def test_login_follows_safe_next_parameter(client, admin_user):
    resp = client.post(
        "/login?next=/admin/groups", data={"email": admin_user.email, "password": "password123"}
    )
    assert resp.headers["Location"].endswith("/admin/groups")


def test_login_ignores_external_next_parameter(client, admin_user):
    resp = client.post(
        "/login?next=https://evil.example.com/",
        data={"email": admin_user.email, "password": "password123"},
    )
    assert "evil.example.com" not in resp.headers["Location"]


def test_role_change_takes_effect_without_relogin(db, user_client, end_user):
    """Access checks use the database role, not a stale copy in the cookie."""
    end_user.role = "admin"
    db.session.commit()
    assert user_client.get("/admin/dashboard").status_code == 200


def test_annotator_is_not_sent_to_an_admin_page_after_login(client, end_user):
    """A leftover ?next=/admin/... link must not end in 'Access denied'."""
    resp = client.post("/login?next=/admin/dashboard", data={"email": end_user.email, "password": "password123"})
    assert resp.headers["Location"].endswith("/user/dashboard")


def test_side_panel_shows_a_summary_and_the_account_for_each_role(app, user_client, admin_client, make_project_admin, db):
    from application.models import Project

    member = user_client.get("/user/dashboard").data
    assert b"Your progress" in member and b"Sign out" in member and b"Member" in member
    admin = admin_client.get("/admin/dashboard").data
    assert b"This week" in admin and b"Answers in 7 days" in admin

    project = Project(name="P", desc="d")
    db.session.add(project)
    db.session.commit()
    client = app.test_client()
    login(client, make_project_admin(project).email)
    assert b"Your projects this week" in client.get("/admin/my-projects").data
