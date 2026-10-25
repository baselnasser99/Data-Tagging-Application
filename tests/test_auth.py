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
    resp = user_client.post("/logout", follow_redirects=True)
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
    assert b"Needs you" in admin and b"Answers in 7 days" not in admin  # what waits, not the dashboard's numbers again

    project = Project(name="P", desc="d", is_annotation_project=True)
    db.session.add(project)
    db.session.commit()
    client = app.test_client()
    login(client, make_project_admin(project).email)
    manager = client.get("/admin/my-projects").data
    assert b"Needs you" in manager and b"P: add items" in manager


def test_needs_you_lists_each_projects_next_step_and_loose_people(app, db, admin_client, make_user, make_project_admin):
    from application.models import AnnotationTask, Project, ProjectImage, Question, UserGroup
    from application.models import AnswerType

    no_items = Project(name="Empty", desc="d", is_annotation_project=True)
    to_hand_out = Project(name="Ready", desc="d", is_annotation_project=True)
    survey = Project(name="Poll", desc="d", is_annotation_project=False)
    db.session.add_all([no_items, to_hand_out, survey, UserGroup(name="Nobody yet")])
    db.session.flush()
    db.session.add_all([
        ProjectImage(project_id=to_hand_out.id, filename="a.png", original_filename="a.png", file_path="x"),
        Question(question="Shape?", project_id=to_hand_out.id, answer_type=AnswerType.TEXT),
        Question(question="Happy?", project_id=survey.id, answer_type=AnswerType.TEXT),
    ])
    db.session.commit()
    make_user(name="Loose")  # a member in no group

    page = admin_client.get("/admin/dashboard").data.decode()
    for text in ("Empty: add items", "Ready: hand out tasks", "Poll: give it to a group",
                 "1 person in no group", "Nobody yet: no members yet"):
        assert text in page, text
    assert "and 3 more" in page  # two shown, the rest folded away
    assert "/admin/users?show=none" in page

    # Once the tasks exist, that step is done
    db.session.add(AnnotationTask(project_id=to_hand_out.id, image_id=to_hand_out.images[0].id,
                                  question_id=to_hand_out.questions[0].id))
    db.session.commit()
    assert "Ready: hand out tasks" not in admin_client.get("/admin/dashboard").data.decode()

    # A manager sees only their own projects, and nothing about people or groups
    client = app.test_client()
    login(client, make_project_admin(survey).email)
    page = client.get("/admin/my-projects").data.decode()
    assert "Poll: give it to a group" in page
    assert "Empty: add items" not in page and "no group" not in page and "Nobody yet" not in page


def test_needs_you_stays_short_with_many_projects(db, admin_client):
    from application.models import Project, UserGroup

    db.session.add_all([Project(name=f"New {i}", desc="d", is_annotation_project=True) for i in range(15)])
    db.session.add_all([UserGroup(name="Empty A"), UserGroup(name="Empty B")])
    db.session.commit()
    page = admin_client.get("/admin/dashboard").data.decode()
    assert "2 groups with no members" in page  # one line for many groups, shown before the projects
    assert page.index("2 groups with no members") < page.index("New 0: add items")
    # 16 lines: the groups line and New 0 shown, New 1-8 folded (10 in all), the last 6 only counted
    assert "New 8: add items" in page and "New 9: add items" not in page
    assert "and 14 more</summary>" in page and "and 6 more: see all projects" in page


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


# ---- sign-in doesn't tell a stranger which emails have accounts -----------

def test_an_unknown_email_pauses_like_a_real_one(app, make_user):
    from application.auth import MAX_FAILED_LOGINS

    make_user(email="real@example.com")
    for email in ("real@example.com", "nobody@example.com"):
        for _ in range(MAX_FAILED_LOGINS):
            _login_from(app, ATTACKER, email, "wrong-guess")
        _, resp = _login_from(app, ATTACKER, email, "wrong-guess")
        assert b"Too many wrong passwords" in resp.data, email


def test_every_failed_sign_in_checks_one_password_hash(app, make_user, monkeypatch):
    # Skipping the slow hash for unknown emails made them answer about 40 times faster.
    import application.auth as auth_module
    from application import models

    calls = []
    for module in (auth_module, models):
        real = module.check_password_hash
        monkeypatch.setattr(module, "check_password_hash",
                            lambda stored, given, real=real: calls.append(1) or real(stored, given))
    removed = make_user(email="removed@example.com")
    removed.active = False
    make_user(email="real@example.com")
    for email in ("real@example.com", "nobody@example.com", "removed@example.com"):
        calls.clear()
        _login_from(app, OWNER, email, "wrong-guess")
        assert len(calls) == 1, email


def test_older_databases_get_the_failure_email_column(app, db):
    from sqlalchemy import inspect, text

    from application import _upgrade_schema

    with db.engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_login_failure_email"))
        conn.execute(text("ALTER TABLE login_failure DROP COLUMN email"))
    _upgrade_schema(app)
    assert "email" in {c["name"] for c in inspect(db.engine).get_columns("login_failure")}


# ---- a copied session cookie stops working --------------------------------

def _copy_of_cookie(app, client):
    """A second browser holding a copy of ``client``'s session cookie, as a thief would."""
    thief = app.test_client()
    thief.set_cookie("session", client.get_cookie("session").value)
    return thief


def test_signing_out_ends_every_copy_of_the_session(app, user_client):
    thief = _copy_of_cookie(app, user_client)
    user_client.post("/logout")
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
