"""Uploaded files are served only to admins, the project's managers and members of its groups."""
import os

import pytest

from application.uploads import upload_dir, upload_path
from application.models import GroupProject, Project, Question, UserGroup, UserGroupMembership
from tests.conftest import BufferedClient, login


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


def _item_url(db, project):
    from application.models import ProjectImage
    image = ProjectImage(project_id=project.id, filename="photo.png", original_filename="red_stop_sign.png", file_path="x")
    db.session.add(image)
    db.session.commit()
    return f"/item/{image.id}"


def test_items_by_number_follow_the_same_rule(app, db, item, make_project_admin):
    url = _item_url(db, item["project"])
    resp = _client(app, item["member"]).get(url)
    assert resp.status_code == 200 and resp.data == b"file-bytes"
    assert _client(app, item["outsider"]).get(url).status_code == 403
    assert _client(app, make_project_admin(item["other_project"])).get(url).status_code == 403
    assert _client(app, item["member"]).get("/item/99999").status_code == 403  # no hint that it doesn't exist


def test_item_pages_do_not_put_the_file_name_in_the_address(app, db, item):
    from flask import render_template_string
    url = _item_url(db, item["project"])
    from application.models import ProjectImage
    image = db.session.get(ProjectImage, int(url.rsplit("/", 1)[1]))
    with app.test_request_context():
        html = render_template_string('{% from "includes/media.html" import media_item %}'
                                      '{{ media_item(project, item, label="Picture to label") }}',
                                      project=item["project"], item=image)
    assert url in html and "red_stop_sign" not in html and "photo.png" not in html


def test_survey_attachments_follow_the_same_rule(app, db, make_user):
    survey, member = _project_with_member(db, make_user, "Survey", survey=True)
    _other, outsider = _project_with_member(db, make_user, "Other", survey=True)
    db.session.add(Question(question="What is this?", project_id=survey.id, file_path="attached.png"))
    db.session.commit()
    _write(upload_path("attached.png"))

    assert _client(app, member).get("/files/attached.png").status_code == 200
    assert _client(app, outsider).get("/files/attached.png").status_code == 403


@pytest.fixture
def static_uploads_app(tmp_path):
    """The Docker layout: uploads inside the static folder (UPLOAD_ROOT not set)."""
    from application import create_app
    from application.config import TestingConfig
    from application.extns import db as _db

    class Config(TestingConfig):
        UPLOAD_ROOT = None

    app = create_app(Config)
    app.test_client_class = BufferedClient
    app.static_folder = str(tmp_path / "static")
    for name, data in (("uploads/images/1/photo.png", b"private"), ("css/site.css", b"public")):
        os.makedirs(os.path.dirname(tmp_path / "static" / name), exist_ok=True)
        _write(tmp_path / "static" / name, data)
    with app.app_context():
        yield app
        _db.session.remove()
        _db.drop_all()
        _db.engine.dispose()


@pytest.mark.parametrize("path", [
    "/static/uploads/images/1/photo.png",
    "/static/./uploads/images/1/photo.png",
    "/static/%2E/uploads/images/1/photo.png",
    "/static/css/../uploads/images/1/photo.png",
    "/static/css/%2e%2e/uploads/images/1/photo.png",
    "/static/UPLOADS/images/1/photo.png",
])
def test_static_route_never_serves_uploads(static_uploads_app, path):
    # The guard checked the raw name, while the static view resolves "./" and "../" before opening
    # the file: anyone could read uploads with /static/./uploads/...
    resp = static_uploads_app.test_client().get(path)
    assert resp.status_code == 404 and b"private" not in resp.data


def test_static_route_still_serves_other_files(static_uploads_app):
    assert static_uploads_app.test_client().get("/static/css/site.css").data == b"public"
