"""End-to-end test of the image annotation workflow described in thesis ch. 6.5."""
import csv
import io
import os
import zipfile

import pytest

from application.models import (
    AnnotationTask,
    Project,
    ProjectImage,
    QuestionAnswer,
    TaskAssignment,
    UserGroup,
    UserGroupMembership,
)
from tests.conftest import login, make_zip


@pytest.fixture
def group_with_members(db, make_user):
    group = UserGroup(name="Annotators")
    db.session.add(group)
    db.session.commit()
    members = [make_user() for _ in range(2)]
    for m in members:
        db.session.add(UserGroupMembership(user_id=m.id, group_id=group.id))
    db.session.commit()
    return group, members


def _create_project(admin_client, answers_per_task=1, max_per_user=100):
    resp = admin_client.post(
        "/admin/create-annotation-project",
        data={
            "project_name": "Street scenes",
            "project_desc": "Describe each photo",
            "max_questions_per_user": str(max_per_user),
            "answers_per_task": str(answers_per_task),
            "total_questions": "2",
            "question_1": "What is in the picture?",
            "question_2": "Is it day or night?",
        },
    )
    assert resp.status_code == 302
    return Project.query.filter_by(name="Street scenes").one()


def _upload(admin_client, project, files):
    return admin_client.post(
        f"/admin/upload-images/{project.id}",
        data={"image_zip": (make_zip(files), "images.zip")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


IMAGES = {
    "a.png": b"png-a",
    "nested/folder/b.jpg": b"jpg-b",
    "c.jpeg": b"jpeg-c",
    "notes.txt": b"ignored",
    "__MACOSX/._a.png": b"junk",
}


def test_create_annotation_project_stores_questions(admin_client):
    project = _create_project(admin_client, answers_per_task=2, max_per_user=50)
    assert project.is_annotation_project
    assert project.answers_per_task == 2 and project.max_questions_per_user == 50
    assert [q.question for q in project.questions] == ["What is in the picture?", "Is it day or night?"]


def test_zip_upload_indexes_images_and_flattens_folders(app, admin_client):
    project = _create_project(admin_client)
    resp = _upload(admin_client, project, IMAGES)
    assert b"Successfully uploaded 3 images" in resp.data

    images = ProjectImage.query.filter_by(project_id=project.id).all()
    assert sorted(i.original_filename for i in images) == ["a.png", "b.jpg", "c.jpeg"]
    for img in images:
        assert os.path.isfile(img.file_path)
        assert os.path.dirname(img.file_path).endswith(os.path.join("images", str(project.id)))


def test_zip_upload_skips_duplicate_file_names(admin_client):
    project = _create_project(admin_client)
    _upload(admin_client, project, {"a.png": b"1"})
    resp = _upload(admin_client, project, {"a.png": b"2", "d.png": b"3"})
    assert b"Skipped 1 file already in this project" in resp.data
    assert ProjectImage.query.filter_by(project_id=project.id).count() == 2


def test_upload_rejects_invalid_zip(admin_client):
    project = _create_project(admin_client)
    resp = admin_client.post(
        f"/admin/upload-images/{project.id}",
        data={"image_zip": (io.BytesIO(b"not a zip"), "bad.zip")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"not a valid ZIP" in resp.data


def test_generate_tasks_creates_item_question_pairs_and_assigns_them(admin_client, group_with_members):
    group, members = group_with_members
    project = _create_project(admin_client, answers_per_task=1)
    _upload(admin_client, project, IMAGES)

    resp = admin_client.post(
        f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id}, follow_redirects=True
    )
    assert resp.status_code == 200
    tasks = AnnotationTask.query.filter_by(project_id=project.id).all()
    assert len(tasks) == 3 * 2  # images x questions

    assignments = TaskAssignment.query.all()
    assert len(assignments) == len(tasks)  # one answer needed per task
    per_user = {m.id: 0 for m in members}
    for a in assignments:
        per_user[a.user_id] += 1
    # handed out per image (2 questions each), so loads differ by at most one image
    assert max(per_user.values()) - min(per_user.values()) <= 2

    # The group is now linked to the project, so it shows on dashboards.
    from application.models import GroupProject
    assert GroupProject.query.filter_by(group_id=group.id, project_id=project.id).count() == 1


def test_full_annotation_cycle_and_export(app, admin_client, group_with_members):
    group, members = group_with_members
    project = _create_project(admin_client)
    _upload(admin_client, project, {"a.png": b"x"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})

    annotator = members[0]
    assignment = TaskAssignment.query.filter_by(user_id=annotator.id).first()
    assert assignment is not None

    client = app.test_client()
    login(client, annotator.email)
    assert client.get("/user/annotation-tasks").status_code == 200
    assert client.get(f"/user/annotation-task/{assignment.id}").status_code == 200

    resp = client.post(f"/user/annotation-task/{assignment.id}", data={"answer": "A red car"}, follow_redirects=True)
    assert b"Answer submitted successfully" in resp.data
    task = assignment.task
    assert assignment.is_completed and task.current_answers == 1 and task.is_completed

    # Submitting the same task twice must not count twice.
    client.post(f"/user/annotation-task/{assignment.id}", data={"answer": "again"})
    assert task.current_answers == 1
    assert QuestionAnswer.query.filter_by(task_assignment_id=assignment.id).count() == 1

    # Another annotator cannot open someone else's task.
    other = app.test_client()
    login(other, members[1].email)
    assert other.get(f"/user/annotation-task/{assignment.id}").status_code == 403

    # Admin export contains the answer.
    resp = admin_client.get(f"/admin/download-answers/{annotator.id}")
    assert resp.mimetype == "text/csv"
    rows = list(csv.reader(io.StringIO(resp.data.decode())))
    assert rows[0][0] == "User Email"
    assert any("A red car" in r for r in rows)
    assert not any("No answers found" in " ".join(r) for r in rows)

    # Progress page reflects the completed task.
    resp = admin_client.get(f"/admin/view-annotation-project/{project.id}")
    assert resp.status_code == 200


def test_export_for_user_without_answers(admin_client, end_user):
    resp = admin_client.get(f"/admin/download-answers/{end_user.id}")
    assert resp.status_code == 200
    assert b"No answers found" in resp.data


def test_annotate_image_batch_view(app, admin_client, group_with_members):
    group, members = group_with_members
    project = _create_project(admin_client)
    _upload(admin_client, project, {"a.png": b"x"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})

    annotator = members[0]
    mine = TaskAssignment.query.filter_by(user_id=annotator.id).all()
    image_id = mine[0].task.image_id
    client = app.test_client()
    login(client, annotator.email)
    assert client.get(f"/user/annotate-image/{image_id}/{project.id}").status_code == 200

    data = {f"answer_{a.id}": f"answer {a.id}" for a in mine}
    client.post(f"/user/annotate-image/{image_id}/{project.id}", data=data)
    assert all(a.is_completed for a in mine)


def test_answering_an_image_in_several_batches_skips_nothing(app, db, admin_client, group_with_members):
    """Regression: page 2 used to skip questions because answered ones dropped out of the list."""
    group, members = group_with_members
    resp = admin_client.post("/admin/create-annotation-project", data={
        "project_name": "Batches", "project_desc": "d", "answers_per_task": "1",
        "max_questions_per_user": "100", "total_questions": "3", "hand_out": "item",
        "question_1": "A?", "question_2": "B?", "question_3": "C?",
    })
    project = Project.query.filter_by(name="Batches").one()
    project.questions_per_image = 1  # one question per page
    db.session.commit()
    _upload(admin_client, project, {"one.png": b"x"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})

    mine = TaskAssignment.query.all()
    owner = mine[0].user
    assert all(a.user_id == owner.id for a in mine)  # one image -> one person
    client = app.test_client()
    login(client, owner.email)
    image_id = mine[0].task.image_id
    base = f"/user/annotate-image/{image_id}/{project.id}"
    for step in range(3):
        page = client.get(f"/user/annotate-image/{image_id}/{project.id}")
        open_ids = [a.id for a in TaskAssignment.query.filter_by(is_completed=False)]
        resp = client.post(f"/user/annotate-image/{image_id}/{project.id}", data={f"answer_{open_ids[0]}": "x"})
        # next batch is the start of what is left, never "page 2"
        if step < 2:
            assert resp.headers["Location"].endswith(base)
    assert all(a.is_completed for a in TaskAssignment.query.all())
    assert b"Done" in client.get("/user/annotation-tasks").data


def test_hand_out_setting_from_the_forms(admin_client):
    project = _create_project(admin_client)
    assert project.hand_out_per_item is False  # default: each question on its own
    admin_client.post(f"/admin/project/{project.id}/edit-questions", data={
        "action": "update_project_settings", "hand_out": "item",
        "answers_per_task": "1", "max_questions_per_user": "100", "questions_per_image": "2",
    })
    assert project.hand_out_per_item is True
    assert b"All of an item&#39;s questions to one person" in admin_client.get(
        f"/admin/view-annotation-project/{project.id}").data


def test_annotator_gets_one_card_per_task_and_moves_to_the_next(app, admin_client, group_with_members):
    group, members = group_with_members
    project = _create_project(admin_client)  # 2 questions
    _upload(admin_client, project, {"a.png": b"a", "b.png": b"b", "c.png": b"c"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})

    annotator = members[0]
    client = app.test_client()
    login(client, annotator.email)

    mine = TaskAssignment.query.filter_by(user_id=annotator.id).all()
    total = len(mine)
    page = client.get("/user/my-projects").data
    assert f"0 of {total} tasks done".encode() in page and b"Not started" in page
    assert f"/user/annotation-tasks?project_id={project.id}".encode() in page
    assert b"Start tasks" in page

    # One card (one answer link) per task, even when two tasks share a photo.
    tasks_page = client.get(f"/user/annotation-tasks?project_id={project.id}").data
    for a in mine:
        assert f'href="/user/annotation-task/{a.id}"'.encode() in tasks_page
    assert b"/user/annotate-image/" not in tasks_page

    order = sorted(mine, key=lambda a: (a.task.image_id, a.task.question_id))
    for i, a in enumerate(order):
        resp = client.post(f"/user/annotation-task/{a.id}", data={"answer": "x"})
        if i + 1 < len(order):  # straight on to the next task, not back to the list
            assert resp.headers["Location"].endswith(f"/user/annotation-task/{order[i + 1].id}")
        if i == 0:
            assert f"1 of {total} tasks done".encode() in client.get("/user/my-projects").data
    assert resp.headers["Location"].endswith(f"/user/annotation-tasks?project_id={project.id}")
    final = client.get("/user/my-projects").data
    assert b"Review tasks" in final and b"Done" in final
    assert f"{total} of {total} tasks done".encode() in client.get("/user/dashboard").data  # the overview shows the card too


def test_task_list_can_show_one_project_only(app, admin_client, group_with_members):
    group, members = group_with_members
    project = _create_project(admin_client)
    _upload(admin_client, project, {"a.png": b"a"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})
    admin_client.post("/admin/create-annotation-project", data={
        "project_name": "Birds", "project_desc": "d", "answers_per_task": "1",
        "max_questions_per_user": "100", "total_questions": "1", "question_1": "Which bird?",
    })
    birds = Project.query.filter_by(name="Birds").one()
    _upload(admin_client, birds, {"robin.png": b"r"})
    admin_client.post(f"/admin/generate-annotation-tasks/{birds.id}", data={"group_id": group.id})

    owner = TaskAssignment.query.join(AnnotationTask).filter(AnnotationTask.project_id == birds.id).first().user
    client = app.test_client()
    login(client, owner.email)
    page = client.get(f"/user/annotation-tasks?project_id={birds.id}").data
    assert b"Which bird?" in page and b"Is it day or night?" not in page
    assert b"Show tasks from all my projects" in page
    # A project the user has no tasks in shows nothing, not even its name.
    stranger = app.test_client()
    login(stranger, [m for m in members if m.id != owner.id][0].email)
    assert b"Birds" not in stranger.get(f"/user/annotation-tasks?project_id={birds.id}").data


def test_members_see_the_project_and_question_not_file_names(app, admin_client, group_with_members):
    group, members = group_with_members
    project = _create_project(admin_client)
    _upload(admin_client, project, {"IMG_20240101_123456.png": b"a"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})
    client = app.test_client()
    login(client, members[0].email)
    mine = TaskAssignment.query.filter_by(user_id=members[0].id).first()
    for path in ("/user/annotation-tasks", f"/user/annotation-task/{mine.id}"):
        page = client.get(path).data
        assert b"Street scenes" in page
        assert b">IMG_20240101_123456.png" not in page and b"Image:</strong>" not in page  # alt text may keep it


def test_zip_that_unpacks_too_large_is_skipped(app, admin_client):
    project = _create_project(admin_client)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.png", b"\0" * (3 * 1024 * 1024))  # compresses to a few KB
    buf.seek(0)
    resp = admin_client.post(
        f"/admin/upload-images/{project.id}",
        data={"image_zip": (buf, "bomb.zip")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"unpacks to more than 2 MB" in resp.data
    assert ProjectImage.query.filter_by(project_id=project.id).count() == 0


def test_member_overview_opens_the_next_task_directly(app, admin_client, group_with_members):
    group, members = group_with_members
    project = _create_project(admin_client)
    _upload(admin_client, project, {"a.png": b"x", "b.png": b"y"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})

    annotator = members[0]
    mine = (TaskAssignment.query.filter_by(user_id=annotator.id).join(AnnotationTask)
            .order_by(AnnotationTask.image_id, AnnotationTask.question_id).all())
    client = app.test_client()
    login(client, annotator.email)

    page = client.get("/user/dashboard").data.decode()
    assert "Up next" in page and f'href="/user/annotation-task/{mine[0].id}"' in page
    assert mine[0].task.question.question in page

    for a in mine:
        client.post(f"/user/annotation-task/{a.id}", data={"answer": "x"})
    page = client.get("/user/dashboard").data.decode()
    assert "Up next" not in page and "You are all caught up" in page


def test_task_page_shows_where_the_member_is(app, admin_client, group_with_members):
    group, members = group_with_members
    project = _create_project(admin_client)
    _upload(admin_client, project, {"a.png": b"x"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})

    annotator = members[0]
    mine = TaskAssignment.query.filter_by(user_id=annotator.id).all()
    client = app.test_client()
    login(client, annotator.email)

    page = client.get(f"/user/annotation-task/{mine[0].id}").data.decode()
    from application.jinja_utils import plural
    assert f"Task 1 of {len(mine)}" in page and f"0 of {plural(len(mine), 'task')} done" in page
    assert 'src="/static/js/answer.js"' in page  # keyboard shortcuts, from our own file (CSP)

    client.post(f"/user/annotation-task/{mine[0].id}", data={"answer": "x"})
    page = client.get(f"/user/annotation-task/{mine[0].id}").data.decode()
    assert "You have already answered this one" in page and "<textarea" not in page
