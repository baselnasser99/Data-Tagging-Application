import io

from application.models import User, UserGroup, UserGroupMembership


def _csv(text):
    return (io.BytesIO(text.encode()), "users.csv")


def test_csv_import_creates_users_and_adds_them_to_group(admin_client, db):
    group = UserGroup(name="Class")
    db.session.add(group)
    db.session.commit()

    data = {
        "csv_file": _csv("full_name,email,password\nAda,ada@example.com,secret123\nAlan,alan@example.com,secret456\n"),
        "user_group_id": str(group.id),
    }
    resp = admin_client.post("/admin/import-users", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert b"2 users created" in resp.data
    ada = User.query.filter_by(email="ada@example.com").one()
    assert ada.role == "end-user" and ada.check_password("secret123")
    assert UserGroupMembership.query.filter_by(group_id=group.id).count() == 2


def test_csv_import_skips_duplicates_and_invalid_rows(admin_client, end_user):
    data = {
        "csv_file": _csv(f"full_name,email,password\nDup,{end_user.email},secret123\n,missing@example.com,x\n"),
    }
    resp = admin_client.post("/admin/import-users", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert b"0 users created, 1 duplicate skipped, 1 invalid row skipped" in resp.data


def test_csv_import_replaces_short_password_and_shows_it_to_admin(admin_client):
    import re

    data = {"csv_file": _csv("full_name,email,password\nShort,short@example.com,hello\n")}
    resp = admin_client.post("/admin/import-users", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert b"shorter than 8 characters" in resp.data
    temp = re.search(rb'<code class="temporary-password">([^<]+)</code>', resp.data).group(1).decode()
    user = User.query.filter_by(email="short@example.com").one()
    assert not user.check_password("hello")
    assert user.check_password(temp)  # the password shown actually works


def test_csv_import_creates_password_when_missing(admin_client):
    data = {"csv_file": _csv("full_name,email,password\nNone,none@example.com,\n")}
    resp = admin_client.post("/admin/import-users", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert b"had no password" in resp.data


def test_csv_import_requires_expected_columns(admin_client):
    data = {"csv_file": _csv("name,mail\nA,a@example.com\n")}
    resp = admin_client.post("/admin/import-users", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert b"must contain" in resp.data
    assert User.query.filter_by(email="a@example.com").first() is None


def test_csv_import_rejects_non_csv(admin_client):
    data = {"csv_file": (io.BytesIO(b"x"), "users.txt")}
    resp = admin_client.post("/admin/import-users", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert b"Invalid file type" in resp.data


def test_csv_import_into_a_group_hands_out_the_groups_work(admin_client, db):
    from application.models import (AnnotationTask, GroupProject, Project, ProjectImage, Question,
                                    TaskAssignment)

    group = UserGroup(name="Class")
    project = Project(name="Photos", desc="d", is_annotation_project=True, answers_per_task=1)
    db.session.add_all([group, project])
    db.session.flush()
    question = Question(question="What?", project_id=project.id)
    images = [ProjectImage(project_id=project.id, filename=f"{i}.png", original_filename=f"{i}.png",
                           file_path=f"/x/{i}.png") for i in range(4)]
    db.session.add_all([question, *images, GroupProject(group_id=group.id, project_id=project.id)])
    db.session.flush()
    db.session.add_all([AnnotationTask(project_id=project.id, image_id=img.id, question_id=question.id,
                                       required_answers=1) for img in images])
    db.session.commit()

    data = {
        "csv_file": _csv("full_name,email,password\nAda,ada@example.com,secret123\nAlan,alan@example.com,secret456\n"),
        "user_group_id": str(group.id),
    }
    admin_client.post("/admin/import-users", data=data, content_type="multipart/form-data")
    for email in ("ada@example.com", "alan@example.com"):
        user = User.query.filter_by(email=email).one()
        assert TaskAssignment.query.filter_by(user_id=user.id).count() == 2  # 4 tasks shared by 2 people


def test_import_page_is_a_tab_of_add_users(admin_client):
    resp = admin_client.get("/admin/import-users")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/create-user?tab=csv")
    page = admin_client.get("/admin/create-user?tab=csv").data
    assert b'name="csv_file"' in page and b'name="full_name"' not in page
    assert b'name="full_name"' in admin_client.get("/admin/create-user").data


def test_csv_import_skips_bad_emails_and_long_names_but_keeps_the_rest(admin_client):
    rows = "full_name,email,password\nAda,ada@example.com,secret123\nNo At,not-an-email,secret123\n" + "x" * 101 + ",long@example.com,secret123\n"
    resp = admin_client.post("/admin/import-users", data={"csv_file": _csv(rows)},
                             content_type="multipart/form-data", follow_redirects=True)
    assert b"Row 3: needs a valid email address" in resp.data and b"Row 4: needs a valid email address" in resp.data
    assert User.query.filter_by(email="ada@example.com").one()
    assert not User.query.filter(User.email.in_(["not-an-email", "long@example.com"])).count()
