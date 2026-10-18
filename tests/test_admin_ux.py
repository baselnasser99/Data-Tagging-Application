"""Admin conveniences: add one user by hand, loose-file uploads, group pre-selection, idle warnings."""
import io

from application.jinja_utils import plural
from application.models import ProjectImage, TaskAssignment, User, UserGroup, UserGroupMembership
from tests.conftest import login
from tests.test_annotation_flow import _create_project, _upload


def test_plural():
    assert plural(1, "image") == "1 image"
    assert plural(0, "image") == "0 images"
    assert plural(2, "person", "people") == "2 people"


def test_create_user_by_hand(app, admin_client):
    resp = admin_client.post("/admin/create-user", data={
        "full_name": "Ada Lovelace", "email": "ada@example.com", "password": "secret123",
    }, follow_redirects=True)
    assert b"Ada Lovelace was created" in resp.data
    user = User.query.filter_by(email="ada@example.com").one()
    assert user.role == "end-user" and user.check_password("secret123")

    client = app.test_client()
    assert login(client, "ada@example.com", "secret123").status_code == 302

    again = admin_client.post("/admin/create-user", data={
        "full_name": "Ada", "email": "ada@example.com", "password": "secret123",
    })
    assert b"already exists" in again.data
    short = admin_client.post("/admin/create-user", data={
        "full_name": "Bob", "email": "bob@example.com", "password": "123",
    })
    assert b"at least 6 characters" in short.data
    assert User.query.filter_by(email="bob@example.com").first() is None


def test_create_user_without_password_shows_a_temporary_one(admin_client):
    resp = admin_client.post("/admin/create-user", data={
        "full_name": "Cai", "email": "cai@example.com", "password": "",
    }, follow_redirects=True)
    assert b"temporary password" in resp.data


def test_new_user_who_gets_nothing_is_reported(db, admin_client, make_user):
    """1 photo x 1 question, answered by 1 person: whoever joins later has nothing to do."""
    group = UserGroup(name="Team")
    db.session.add(group)
    db.session.commit()
    first = make_user()
    db.session.add(UserGroupMembership(user_id=first.id, group_id=group.id))
    db.session.commit()
    admin_client.post("/admin/create-annotation-project", data={
        "project_name": "Tiny", "project_desc": "d", "answers_per_task": "1",
        "max_questions_per_user": "100", "total_questions": "1", "question_1": "What?",
    })
    from application.models import Project
    project = Project.query.filter_by(name="Tiny").one()
    _upload(admin_client, project, {"a.png": b"a"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})
    assert TaskAssignment.query.filter_by(user_id=first.id).count() == 1

    resp = admin_client.post("/admin/create-user", data={
        "full_name": "Late Joiner", "email": "late@example.com", "password": "secret123", "group_id": group.id,
    }, follow_redirects=True)
    assert b"added to group" in resp.data
    assert b"Late Joiner got no tasks in" in resp.data
    assert b"Answers per task" in resp.data

    # The Hand out tasks page now pre-selects the group already on the project.
    page = admin_client.get(f"/admin/generate-annotation-tasks/{project.id}").data
    assert b"selected" in page and b"already working on this project" in page


def test_upload_loose_files_and_zips_together(admin_client):
    project = _create_project(admin_client)
    resp = admin_client.post(
        f"/admin/upload-images/{project.id}",
        data={"image_zip": [
            (io.BytesIO(b"png"), "loose.png"),
            (io.BytesIO(b"mp3"), "clip.mp3"),
            (io.BytesIO(b"txt"), "notes.txt"),
        ]},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Successfully uploaded 1 image and 1 audio file" in resp.data
    assert b"notes.txt" in resp.data  # reported as skipped
    assert sorted(i.original_filename for i in ProjectImage.query.filter_by(project_id=project.id)) == [
        "clip.mp3", "loose.png"]

    # A ZIP in the same field still works, and names already in the project are skipped.
    resp = _upload(admin_client, project, {"loose.png": b"again", "zipped.jpg": b"jpg"})
    assert b"Successfully uploaded 1 image" in resp.data
    assert b"Skipped 1 file already in this project" in resp.data


def test_dashboard_chart_data_and_group_progress(app, db, admin_client, make_user):
    """The chart's JSON must survive the HTML attribute, and progress must count annotation tasks."""
    import json
    import re

    group = UserGroup(name="Team")
    db.session.add(group)
    db.session.commit()
    people = [make_user(), make_user()]
    for p in people:
        db.session.add(UserGroupMembership(user_id=p.id, group_id=group.id))
    db.session.commit()
    project = _create_project(admin_client)  # 2 questions
    _upload(admin_client, project, {"a.png": b"a"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})

    mine = TaskAssignment.query.filter_by(user_id=people[0].id).first()
    client = app.test_client()
    login(client, people[0].email)
    client.post(f"/user/annotation-task/{mine.id}", data={"answer": "x"})

    page = admin_client.get("/admin/dashboard").data.decode()
    weeks = json.loads(re.search(r"data-chart-data='([^']*)'", page).group(1))
    assert len(weeks) == 12 and sum(w["answers"] for w in weeks) == 1
    assert "50.0%" in page  # 1 of the group's 2 tasks done
    assert b"1 of 2 tasks done" in admin_client.get("/admin/assign-project-to-group").data


def test_assigning_an_empty_group_says_so(db, admin_client):
    project = _create_project(admin_client)
    _upload(admin_client, project, {"a.png": b"a"})
    group = UserGroup(name="Nobody yet")
    db.session.add(group)
    db.session.commit()
    resp = admin_client.post("/admin/assign-project-to-group",
                             data={"group_id": group.id, "project_id": project.id}, follow_redirects=True)
    assert b"has no members yet" in resp.data
    assert b"No work yet" in resp.data and b"Pending" not in resp.data  # progress, not the stale flag


def test_exports_show_annotator_names(app, db, admin_client, make_user):
    group = UserGroup(name="Team")
    db.session.add(group)
    db.session.commit()
    ada = make_user(name="Ada Lovelace")
    db.session.add(UserGroupMembership(user_id=ada.id, group_id=group.id))
    db.session.commit()
    project = _create_project(admin_client)
    _upload(admin_client, project, {"a.png": b"a"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})
    client = app.test_client()
    login(client, ada.email)
    for a in TaskAssignment.query.filter_by(user_id=ada.id):
        client.post(f"/user/annotation-task/{a.id}", data={"answer": "x"})

    assert b"Ada Lovelace" in admin_client.get(f"/admin/project/{project.id}/export.csv").data
    assert b"Ada Lovelace" in admin_client.get(f"/admin/download-answers/{ada.id}").data


def test_projects_page_shows_the_whole_description_as_text(admin_client):
    long_desc = "Label street photos. " * 40 + "<b>end</b>"
    admin_client.post("/admin/create-annotation-project", data={
        "project_name": "Streets", "project_desc": long_desc, "answers_per_task": "1",
        "max_questions_per_user": "100", "total_questions": "1", "question_1": "What?",
    })
    page = admin_client.get("/admin/view-projects").data.decode()
    assert "Label street photos. " * 40 in page  # not cut off
    assert "&lt;b&gt;end&lt;/b&gt;" in page  # shown as text, never run as HTML
    assert "1 question" in page and "Survey" not in page


def test_managers_page_add_from_list_or_by_email(admin_client, db, make_user):
    from application.models import Project, ProjectAdmin

    first, second = Project(name="Alpha", desc="d"), Project(name="Beta", desc="d")
    db.session.add_all([first, second])
    db.session.commit()
    ben, cai = make_user(name="Ben Byte"), make_user(name="Cai Code")

    page = admin_client.get("/admin/managers").data
    assert b"No manager" in page and b"Ben Byte" in page  # the people list

    # Pick from the list...
    resp = admin_client.post("/admin/managers/add", data={"project_id": first.id, "user_id": ben.id},
                             follow_redirects=True)
    assert b"Ben Byte now manages" in resp.data and ben.role == "project-admin"
    # ...or type an email, which wins over the list.
    resp = admin_client.post("/admin/managers/add", data={"project_id": second.id, "user_id": ben.id,
                                                          "email": cai.email}, follow_redirects=True)
    assert b"Cai Code now manages" in resp.data
    assert {(pa.project_id, pa.user_id) for pa in ProjectAdmin.query} == {(first.id, ben.id), (second.id, cai.id)}

    # One manager per project.
    resp = admin_client.post("/admin/managers/add", data={"project_id": first.id, "user_id": cai.id},
                             follow_redirects=True)
    assert b"already has a manager" in resp.data
    assert b"Every project already has a manager" in resp.data


def test_managers_page_is_for_admins_only(app, make_user):
    user = make_user()
    client = app.test_client()
    login(client, user.email)
    assert client.get("/admin/managers").status_code == 403


def test_project_manager_page_has_the_picker(admin_client, db, make_user):
    from application.models import Project

    project = Project(name="Gamma", desc="d")
    db.session.add(project)
    db.session.commit()
    make_user(name="Dee Data")
    page = admin_client.get(f"/admin/project/{project.id}/admins").data
    assert b"Dee Data" in page and b'name="email"' in page and b'name="user_id"' in page
    assert b'class="content"' in page  # inside the normal page layout, not glued to the sidebar


def test_create_a_manager_without_a_project(admin_client):
    resp = admin_client.post("/admin/create-user", data={
        "full_name": "Mia Manager", "email": "mia@example.com", "password": "secret123",
        "role": "manager", "group_id": "",
    }, follow_redirects=True)
    assert b"created as a manager" in resp.data
    assert User.query.filter_by(email="mia@example.com").one().role == "project-admin"
    assert b"Managers without a project" in resp.data and b"Mia Manager" in resp.data  # lands on Managers
    assert b"Create a new manager" in resp.data  # the form lives on the Managers page
    assert b'value="manager" checked' in admin_client.get("/admin/create-user?role=manager").data


def test_manager_home_has_the_answers_chart_for_their_projects_only(app, db, make_user, make_project_admin):
    from application.admin import weekly_answers
    from application.models import Project, Question, QuestionAnswer

    mine, other = Project(name="Mine", desc="d"), Project(name="Other", desc="d")
    db.session.add_all([mine, other])
    db.session.flush()
    q_mine, q_other = Question(question="A?", project_id=mine.id), Question(question="B?", project_id=other.id)
    db.session.add_all([q_mine, q_other])
    db.session.flush()
    person = make_user()
    db.session.add_all([QuestionAnswer(user_id=person.id, question_id=q.id, answer="x") for q in (q_mine, q_other, q_other)])
    db.session.commit()

    assert sum(w["answers"] for w in weekly_answers([mine.id])) == 1
    assert sum(w["answers"] for w in weekly_answers()) == 3

    client = app.test_client()
    login(client, make_project_admin(mine).email)
    page = client.get("/admin/my-projects").data
    assert b'id="weeklyChart"' in page and b"Group progress" in page
