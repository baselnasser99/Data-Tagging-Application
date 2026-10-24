"""'Can't tell': a member marks an item they couldn't answer. It finishes the task like any answer,
is exported, and is left out of agreement; managers see the items on the results page and the side panel."""
import csv
import io
import json

from application.models import AnnotationTask, QuestionAnswer, TaskAssignment
from tests.conftest import login
from tests.test_annotation_flow import _create_project, _upload, group_with_members  # noqa: F401 (fixture)


def _two_people_one_item(app, admin_client, group_with_members):  # noqa: F811
    group, members = group_with_members
    project = _create_project(admin_client, answers_per_task=2)
    _upload(admin_client, project, {"a.png": b"x"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})
    clients = []
    for member in members:
        c = app.test_client()
        login(c, member.email)
        clients.append(c)
    return project, members, clients


def _assignment(member, question_text):
    return (TaskAssignment.query.join(AnnotationTask).filter(TaskAssignment.user_id == member.id)
            .filter(AnnotationTask.question.has(question=question_text)).one())


def test_cant_tell_finishes_the_task_and_stays_out_of_the_agreement(app, admin_client, group_with_members):  # noqa: F811
    project, members, (alice, bob) = _two_people_one_item(app, admin_client, group_with_members)
    q = "Is it day or night?"
    a_task, b_task = _assignment(members[0], q), _assignment(members[1], q)

    alice.post(f"/user/annotation-task/{a_task.id}", data={"answer": "Day"})
    resp = bob.post(f"/user/annotation-task/{b_task.id}", data={"cant_tell": "1"}, follow_redirects=True)
    assert b"Marked as &#34;can&#39;t tell&#34;" in resp.data  # the flash message, escaped by Jinja

    # It counts as finishing: nobody else is handed the task
    assert b_task.is_completed and b_task.task.is_completed and b_task.task.current_answers == 2
    saved = QuestionAnswer.query.filter_by(task_assignment_id=b_task.id).one()
    assert saved.cant_tell and saved.answer == "Can't tell"

    data = json.loads(admin_client.get(f"/admin/project/{project.id}/export.json").data)
    task = next(t for image in data["images"] for t in image["questions"] if t["question"] == q)
    assert task["summary"]["answers_received"] == 2 and task["summary"]["cant_tell"] == 1
    assert task["summary"]["majority_answer"] == "day" and task["summary"]["majority_share"] == 1.0  # Bob's isn't a vote
    assert sorted(a["cant_tell"] for a in task["answers"]) == [False, True]

    rows = list(csv.DictReader(io.StringIO(admin_client.get(f"/admin/project/{project.id}/export.csv").data.decode())))
    assert sorted(r["cant_tell"] for r in rows if r["question"] == q) == ["", "yes"]


def test_managers_see_the_items_marked_cant_tell(app, admin_client, group_with_members):  # noqa: F811
    project, members, (alice, bob) = _two_people_one_item(app, admin_client, group_with_members)
    q = "What is in the picture?"
    bob.post(f"/user/annotation-task/{_assignment(members[1], q).id}", data={"cant_tell": "1"})

    page = admin_client.get(f"/admin/view-annotation-project/{project.id}").data.decode()
    assert 'Marked "can\'t tell": 1 item' in page
    assert "· 1 can't tell" in page

    side = admin_client.get("/admin/dashboard").data.decode()
    assert "Street scenes: 1 item marked can" in side
    assert f"/admin/view-annotation-project/{project.id}" in side


def test_cant_tell_on_the_per_item_page_marks_all_its_questions(app, admin_client, group_with_members):  # noqa: F811
    project, members, (alice, _bob) = _two_people_one_item(app, admin_client, group_with_members)
    image_id = project.images[0].id
    page = alice.get(f"/user/annotate-image/{image_id}/{project.id}").data
    assert b"Can't tell for this item" in page

    alice.post(f"/user/annotate-image/{image_id}/{project.id}", data={"cant_tell": "1"})
    mine = TaskAssignment.query.filter_by(user_id=members[0].id).all()
    assert len(mine) == 2 and all(a.is_completed for a in mine)
    assert QuestionAnswer.query.filter_by(user_id=members[0].id, cant_tell=True).count() == 2


def test_older_databases_get_the_new_column(app, db):
    from sqlalchemy import inspect, text

    from application import _upgrade_schema

    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE question_answer DROP COLUMN cant_tell"))
    assert "cant_tell" not in {c["name"] for c in inspect(db.engine).get_columns("question_answer")}
    _upgrade_schema(app)
    assert "cant_tell" in {c["name"] for c in inspect(db.engine).get_columns("question_answer")}
