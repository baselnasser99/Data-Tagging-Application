import pytest

from application.models import Project, UserGroup

STATIC_ADMIN_PAGES = [
    "/admin/dashboard",
    "/admin/users",
    "/admin/create-user?tab=csv",
    "/admin/create-project",
    "/admin/view-projects",
    "/admin/profile",
    "/admin/groups",
    "/admin/create-group",
    "/admin/assign-project-to-group",
    "/admin/create-annotation-project",
]


@pytest.mark.parametrize("path", STATIC_ADMIN_PAGES)
def test_admin_pages_render(admin_client, path):
    assert admin_client.get(path).status_code == 200


@pytest.fixture
def sample_project(db):
    project = Project(name="Survey", desc="A survey project")
    db.session.add(project)
    db.session.commit()
    return project


@pytest.mark.parametrize(
    "path",
    [
        "/admin/project/{id}/dashboard",
        "/admin/project/{id}/admins",
        "/admin/project/{id}/groups",
        "/admin/project/{id}/edit-questions",
    ],
)
def test_project_pages_render_for_admin(admin_client, sample_project, path):
    assert admin_client.get(path.format(id=sample_project.id)).status_code == 200


def test_project_admin_sees_only_their_projects(app, db, make_project_admin, sample_project):
    from tests.conftest import login

    other = Project(name="Other", desc="Not managed")
    db.session.add(other)
    db.session.commit()
    manager = make_project_admin(sample_project)

    client = app.test_client()
    login(client, manager.email)
    assert client.get(f"/admin/project/{sample_project.id}/dashboard").status_code == 200
    assert client.get(f"/admin/project/{other.id}/dashboard").status_code == 403
    assert client.get("/admin/users").status_code == 403  # system-admin only
    resp = client.get("/admin/my-projects")
    assert b"Survey" in resp.data and b"Other" not in resp.data


def test_admin_profile_update_password(admin_client, admin_user):
    resp = admin_client.post(
        "/admin/profile",
        data={
            "action": "update_password",
            "current_password": "password123",
            "new_password": "newpassword",
            "confirm_password": "newpassword",
        },
        follow_redirects=True,
    )
    assert b"Password updated successfully" in resp.data
    assert admin_user.check_password("newpassword")


def test_admin_profile_rejects_wrong_current_password(admin_client, admin_user):
    resp = admin_client.post(
        "/admin/profile",
        data={
            "action": "update_password",
            "current_password": "nope",
            "new_password": "newpassword",
            "confirm_password": "newpassword",
        },
        follow_redirects=True,
    )
    assert b"Current password is incorrect" in resp.data


def test_delete_project_removes_it(admin_client, db, sample_project):
    resp = admin_client.post(f"/admin/delete-project/{sample_project.id}", follow_redirects=True)
    assert b"Project Deleted Successfully" in resp.data
    assert db.session.get(Project, sample_project.id) is None


def test_dashboard_counts(admin_client, db, make_user, sample_project):
    make_user()
    make_user()
    db.session.add(UserGroup(name="G"))
    db.session.commit()
    resp = admin_client.get("/admin/dashboard")
    assert resp.status_code == 200


def test_project_cards_show_status_and_progress(admin_client, db, sample_project):
    from application.models import AnnotationTask, ProjectImage, Question

    photos = Project(name="Photos", desc="d", is_annotation_project=True)
    db.session.add(photos)
    db.session.flush()
    question = Question(question="What?", project_id=photos.id)
    image = ProjectImage(project_id=photos.id, filename="a.png", original_filename="a.png", file_path="/x/a.png")
    db.session.add_all([question, image])
    db.session.flush()
    db.session.add_all([AnnotationTask(project_id=photos.id, image_id=image.id, question_id=question.id,
                                       required_answers=1, is_completed=done) for done in (True, False)])
    db.session.commit()

    page = admin_client.get("/admin/view-projects").data
    assert b"In progress" in page and b"1 of 2 tasks done" in page
    assert b"Needs questions" in page  # the empty survey from sample_project
