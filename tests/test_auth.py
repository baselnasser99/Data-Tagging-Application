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


# ---- wrong passwords pause one address, not the account -------------------

ATTACKER, OWNER = "198.51.100.1", "203.0.113.5"


def _login_from(app, address, email, password="password123"):
    client = app.test_client()
    resp = client.post("/login", data={"email": email, "password": password},
                       environ_base={"REMOTE_ADDR": address}, follow_redirects=True)
    return client, resp


def test_five_wrong_passwords_pause_that_address_even_for_the_right_one(app, make_user):
    from application.auth import MAX_FAILED_LOGINS

    user = make_user(email="target@example.com")
    for _ in range(MAX_FAILED_LOGINS):
        _login_from(app, ATTACKER, user.email, "wrong-guess")
    client, resp = _login_from(app, ATTACKER, user.email)  # the right password, during the pause
    assert b"Too many wrong passwords" in resp.data
    assert client.get("/user/dashboard").status_code == 302  # still signed out


def test_someone_elses_wrong_guesses_do_not_lock_the_owner_out(app, make_user):
    # Before, five wrong guesses from anyone paused the account itself: anyone could keep the admin out.
    from application.auth import MAX_FAILED_LOGINS

    user = make_user(email="target@example.com")
    for _ in range(MAX_FAILED_LOGINS * 2):
        _login_from(app, ATTACKER, user.email, "wrong-guess")
    client, _ = _login_from(app, OWNER, user.email)
    assert client.get("/user/dashboard").status_code == 200


def test_one_address_trying_many_accounts_is_paused(app, make_user):
    from application.auth import MAX_FAILED_LOGINS_PER_ADDRESS

    user = make_user(email="sprayed@example.com")
    for n in range(MAX_FAILED_LOGINS_PER_ADDRESS):  # one guess each, across many accounts
        _login_from(app, ATTACKER, f"person{n}@example.com", "Summer2026!")
    _, resp = _login_from(app, ATTACKER, user.email)
    assert b"Too many failed sign-ins from your network" in resp.data
    client, _ = _login_from(app, OWNER, user.email)
    assert client.get("/user/dashboard").status_code == 200


def test_the_pause_ends_after_lock_minutes(app, db, make_user):
    from datetime import timedelta

    from application.auth import LOCK_MINUTES, MAX_FAILED_LOGINS
    from application.models import LoginFailure

    user = make_user(email="target@example.com")
    for _ in range(MAX_FAILED_LOGINS):
        _login_from(app, ATTACKER, user.email, "wrong-guess")
    for failure in LoginFailure.query.all():
        failure.at -= timedelta(minutes=LOCK_MINUTES + 1)
    db.session.commit()
    client, _ = _login_from(app, ATTACKER, user.email)
    assert client.get("/user/dashboard").status_code == 200


def test_a_right_password_resets_the_count(app, make_user):
    from application.auth import MAX_FAILED_LOGINS

    user = make_user(email="typo@example.com")
    for _ in range(MAX_FAILED_LOGINS - 1):
        _login_from(app, OWNER, user.email, "typo")
    _login_from(app, OWNER, user.email)
    for _ in range(MAX_FAILED_LOGINS - 1):
        _login_from(app, OWNER, user.email, "typo")
    client, _ = _login_from(app, OWNER, user.email)
    assert client.get("/user/dashboard").status_code == 200


def test_a_new_password_from_the_admin_lifts_the_pause(app, admin_client, make_user):
    from application.auth import MAX_FAILED_LOGINS

    user = make_user(email="forgot@example.com")
    for _ in range(MAX_FAILED_LOGINS):
        _login_from(app, OWNER, user.email, "forgotten")
    admin_client.post(f"/admin/users/{user.id}/edit", data={
        "full_name": user.full_name, "email": user.email, "password": "a-fresh-password"})
    client, _ = _login_from(app, OWNER, user.email, "a-fresh-password")
    assert client.get("/user/dashboard").status_code == 200


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


# ---- the audit log keeps one line per event -------------------------------

def _audit_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "audit"]


def test_a_line_break_in_the_email_cannot_add_a_fake_audit_line(caplog, client, end_user):
    caplog.set_level("INFO", logger="audit")
    forged = "\n2026-10-22 09:00:00 LOGIN_OK user_id=1 email=admin@example.com ip=10.0.0.1"
    login(client, end_user.email + forged, password="wrong")
    login(client, "nobody@example.com" + forged, password="wrong")
    assert _audit_lines(caplog) and all("\n" not in line and "\r" not in line for line in _audit_lines(caplog))


def test_every_audit_message_stays_on_one_line(app, caplog):
    # Whatever value is logged later, a line break in it is written as \r\n, not as a new line.
    import logging

    caplog.set_level("INFO", logger="audit")
    logging.getLogger("audit").info("EVENT value=%s", "a\r\n2026-10-22 09:00:00 LOGIN_OK user_id=1")
    assert _audit_lines(caplog) == ["EVENT value=a\\r\\n2026-10-22 09:00:00 LOGIN_OK user_id=1"]


def test_text_typed_as_an_unknown_email_is_not_logged(caplog, client):
    # People sometimes type their password into the email box.
    caplog.set_level("INFO", logger="audit")
    login(client, "MyS3cretPassw0rd", password="whatever")
    lines = _audit_lines(caplog)
    assert any("LOGIN_FAILED" in line for line in lines)
    assert not any("MyS3cretPassw0rd" in line for line in lines)


def test_failed_sign_in_to_a_real_account_names_the_account(caplog, client, end_user):
    caplog.set_level("INFO", logger="audit")
    login(client, end_user.email, password="wrong")
    assert any(f"user_id={end_user.id}" in line and end_user.email in line for line in _audit_lines(caplog))


# ---- password forms with fields left out ----------------------------------

@pytest.mark.parametrize("fields", [{}, {"current_password": "password123"}])
@pytest.mark.parametrize("who, url, extra", [("member", "/user/change-password", {}),
                                             ("admin", "/admin/profile", {"action": "update_password"})])
def test_password_forms_with_missing_fields_say_so_instead_of_crashing(app, user_client, admin_client, end_user,
                                                                        admin_user, who, url, extra, fields):
    # A tampered or half-sent form used to end in a 500 (check_password(None), len(None)).
    client, user = (user_client, end_user) if who == "member" else (admin_client, admin_user)
    app.config["PROPAGATE_EXCEPTIONS"] = False  # see the 500 a visitor would get, not the exception
    resp = client.post(url, data={**extra, **fields}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"incorrect" in resp.data or b"at least" in resp.data
    assert user.check_password("password123")  # unchanged
