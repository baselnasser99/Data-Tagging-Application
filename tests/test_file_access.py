"""Uploaded files are served only to admins, the project's managers and members of its groups."""
import os

import pytest

from application.admin import upload_dir, upload_path
from application.models import GroupProject, Project, Question, UserGroup, UserGroupMembership
from tests.conftest import login


def _project_with_member(db, make_user, name, survey=False):
    """A project, a group assigned to it, and one member of that group."""
    project = Project(name=name, desc="d", is_annotation_project=not survey)
    group = UserGroup(name=f"{name} team")
    db.session.add_all([project, group])
    db.session.flush()
    member = make_user()
    db.session.add_all([GroupProject(group_id=group.id, project_id=project.id),
                        UserGroupMembership(user_id=member.id, group_id=group.id)])
    db.session.commit()
    return project, member


def _write(path, data=b"file-bytes"):
    with open(path, "wb") as f:
        f.write(data)


@pytest.fixture
def item(db, make_user):
    """An item of project A, plus a member of A and a member of another project B."""
    project, member = _project_with_member(db, make_user, "A")
    other_project, outsider = _project_with_member(db, make_user, "B")
    _write(os.path.join(upload_dir("images", str(project.id)), "photo.png"))
    return {"url": f"/files/images/{project.id}/photo.png", "project": project, "member": member,
            "other_project": other_project, "outsider": outsider}


def _client(app, user):
    c = app.test_client()
    login(c, user.email)
    return c


def test_non_member_gets_403_for_another_projects_file(app, item):
    assert _client(app, item["outsider"]).get(item["url"]).status_code == 403


def test_member_gets_200(app, item):
    resp = _client(app, item["member"]).get(item["url"])
    assert resp.status_code == 200 and resp.data == b"file-bytes"


def test_admin_gets_200(admin_client, item):
    assert admin_client.get(item["url"]).status_code == 200


def test_manager_sees_only_their_own_projects_files(app, item, make_project_admin):
    assert _client(app, make_project_admin(item["project"])).get(item["url"]).status_code == 200
    assert _client(app, make_project_admin(item["other_project"])).get(item["url"]).status_code == 403


def test_member_without_any_group_gets_403(user_client, item):
    assert user_client.get(item["url"]).status_code == 403


def test_dot_dot_in_the_name_cannot_reach_another_project(app, item):
    url = f"/files/images/{item['other_project'].id}/../{item['project'].id}/photo.png"
    assert _client(app, item["outsider"]).get(url).status_code == 403


def test_unknown_file_gets_403_not_a_hint_that_it_exists(app, item):
    assert _client(app, item["member"]).get("/files/nothing-here.png").status_code == 403


def test_survey_attachments_follow_the_same_rule(app, db, make_user):
    survey, member = _project_with_member(db, make_user, "Survey", survey=True)
    _other, outsider = _project_with_member(db, make_user, "Other", survey=True)
    db.session.add(Question(question="What is this?", project_id=survey.id, file_path="attached.png"))
    db.session.commit()
    _write(upload_path("attached.png"))

    assert _client(app, member).get("/files/attached.png").status_code == 200
    assert _client(app, outsider).get("/files/attached.png").status_code == 403
