"""End-to-end test of the image annotation workflow described in thesis ch. 6.5."""
import csv
import io
import os

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
    assert b"Skipped 1 files" in resp.data
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
    assert max(per_user.values()) - min(per_user.values()) <= 1  # evenly split


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
