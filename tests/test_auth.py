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


def test_there_is_no_public_sign_up(client):
    # The admin adds people (thesis); nobody can create their own account.
    assert client.get("/register").status_code == 404
    assert b"Create an account" not in client.get("/login").data


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


def test_five_wrong_passwords_pause_the_account_even_for_the_right_one(client, make_user, db):
    from application.auth import MAX_FAILED_LOGINS
    user = make_user(email="target@example.com")
    for _ in range(MAX_FAILED_LOGINS):
        login(client, user.email, password="wrong-guess")
    db.session.refresh(user)
    assert user.locked_until is not None
    resp = login(client, user.email, follow=True)  # the right password, during the pause
    assert b"Too many wrong passwords" in resp.data
    assert client.get("/user/dashboard").status_code == 302  # still signed out

    user.locked_until = None  # the pause is over
    db.session.commit()
    assert login(client, user.email).headers["Location"].endswith("/user/dashboard")
    db.session.refresh(user)
    assert user.failed_logins == 0


def test_a_right_password_resets_the_count(client, make_user, db):
    user = make_user(email="typo@example.com")
    for _ in range(3):
        login(client, user.email, password="typo")
    login(client, user.email)
    db.session.refresh(user)
    assert user.failed_logins == 0 and user.locked_until is None


# ---- a copied session cookie stops working --------------------------------

def _copy_of_cookie(app, client):
    """A second browser holding a copy of ``client``'s session cookie, as a thief would."""
    thief = app.test_client()
    thief.set_cookie("session", client.get_cookie("session").value)
    return thief


def test_signing_out_ends_every_copy_of_the_session(app, user_client):
    thief = _copy_of_cookie(app, user_client)
    user_client.get("/logout")
    assert thief.get("/user/dashboard").status_code == 302


def test_changing_password_ends_other_sessions_but_keeps_this_one(app, user_client):
    thief = _copy_of_cookie(app, user_client)
    user_client.post("/user/change-password", data={
        "current_password": "password123", "new_password": "a-new-password", "confirm_password": "a-new-password"})
    assert thief.get("/user/dashboard").status_code == 302
    assert user_client.get("/user/dashboard").status_code == 200


def test_admin_changing_own_password_keeps_them_signed_in(app, admin_client):
    thief = _copy_of_cookie(app, admin_client)
    admin_client.post("/admin/profile", data={
        "action": "update_password", "current_password": "password123",
        "new_password": "a-new-password", "confirm_password": "a-new-password"})
    assert thief.get("/admin/dashboard").status_code == 302
    assert admin_client.get("/admin/dashboard").status_code == 200


def test_admin_setting_a_members_password_signs_the_member_out(app, admin_client, user_client, end_user):
    admin_client.post(f"/admin/users/{end_user.id}/edit", data={
        "full_name": end_user.full_name, "email": end_user.email, "password": "a-new-password"})
    assert user_client.get("/user/dashboard").status_code == 302


def test_cookies_from_before_session_versions_are_refused(app, end_user):
    old = app.test_client()
    with old.session_transaction() as sess:  # what Flask-Login stored until now: the bare id
        sess["_user_id"] = str(end_user.id)
        sess["_fresh"] = True
    assert old.get("/user/dashboard").status_code == 302
