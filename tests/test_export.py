"""Dataset export (CSV/JSON) and agreement scores."""
import csv
import io
import json

import pytest

from application import distribution
from application.export import fleiss_kappa, interpret_kappa, normalise
from application.models import (
    AnswerType,
    ExportLog,
    GroupProject,
    Project,
    ProjectImage,
    Question,
    QuestionAnswer,
    TaskAssignment,
    UserGroup,
    UserGroupMembership,
)


# ---- the agreement maths ----------------------------------------------------

def test_fleiss_kappa_matches_the_published_example():
    """Fleiss (1971) worked example as reproduced on Wikipedia: kappa = 0.210."""
    table = [
        [0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1],
        [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7],
    ]
    items = [[str(cat) for cat, n in enumerate(row) for _ in range(n)] for row in table]
    assert fleiss_kappa(items) == pytest.approx(0.210, abs=0.001)


def test_perfect_agreement_and_edge_cases():
    assert fleiss_kappa([["Day", "Day"], ["Night", "Night"]]) == 1.0
    assert fleiss_kappa([["Day"]]) is None  # one rating is not agreement
    assert fleiss_kappa([["Day", "Day"], ["Day", "Day"]]) is None  # single category
    assert interpret_kappa(0.7) == "substantial"
    assert interpret_kappa(None) == "not enough data"


def test_normalise_ignores_case_spacing_and_punctuation():
    assert normalise("  Bicycle! ") == normalise("bicycle") == "bicycle"


# ---- end to end ---------------------------------------------------------------

@pytest.fixture
def answered_project(db, make_user):
    project = Project(name="Street scenes", desc="d", is_annotation_project=True,
                      answers_per_task=2, max_questions_per_user=100)
    group = UserGroup(name="Team")
    db.session.add_all([project, group])
    db.session.flush()
    mcq = Question(question="Day or night?", project_id=project.id, answer_type=AnswerType.MCQ,
                   number_of_options=2, option1="Day", option2="Night")
    text = Question(question="Main object?", project_id=project.id)
    db.session.add_all([mcq, text])
    db.session.add_all([
        ProjectImage(project_id=project.id, filename=f"{i}.png", original_filename=f"img{i}.png", file_path="x")
        for i in range(2)
    ])
    db.session.add(GroupProject(group_id=group.id, project_id=project.id))
    users = [make_user(), make_user()]
    for u in users:
        db.session.add(UserGroupMembership(user_id=u.id, group_id=group.id))
    db.session.commit()
    distribution.distribute(project)

    replies = {"Day or night?": ["Day", "Day"], "Main object?": ["Bicycle", "bicycle!"]}
    for a in TaskAssignment.query.all():
        given = replies[a.task.question.question]
        value = given[users.index(a.user)]
        db.session.add(QuestionAnswer(user_id=a.user_id, question_id=a.task.question_id,
                                      answer=value, task_assignment_id=a.id))
        a.is_completed = True
        a.task.current_answers += 1
        a.task.is_completed = a.task.current_answers >= a.task.required_answers
    db.session.commit()
    return project


def test_json_export_structure_and_agreement(admin_client, answered_project):
    resp = admin_client.get(f"/admin/project/{answered_project.id}/export.json")
    assert resp.mimetype == "application/json"
    assert "street-scenes.json" in resp.headers["Content-Disposition"]
    data = json.loads(resp.data)

    assert data["totals"] == {"images": 2, "tasks": 4, "tasks_complete": 4, "answers": 8}
    mcq_summary = next(q for q in data["questions"] if q["type"] == "multiple_choice")
    assert mcq_summary["fleiss_kappa"] is None  # everyone said "Day": single category
    first = data["images"][0]
    by_q = {q["question"]: q for q in first["questions"]}
    assert by_q["Day or night?"]["options"] == ["Day", "Night"]
    assert by_q["Day or night?"]["summary"]["majority_answer"] == "Day"
    assert by_q["Main object?"]["summary"]["majority_share"] == 1.0  # Bicycle == bicycle!
    assert len(by_q["Main object?"]["answers"]) == 2


def test_csv_export_has_one_row_per_answer(admin_client, answered_project):
    resp = admin_client.get(f"/admin/project/{answered_project.id}/export.csv")
    rows = list(csv.DictReader(io.StringIO(resp.data.decode())))
    assert len(rows) == 8
    assert {"file_name", "question", "annotator", "answer", "task_majority_answer"} <= set(rows[0])


def test_exports_are_logged_and_shown(admin_client, answered_project):
    admin_client.get(f"/admin/project/{answered_project.id}/export.csv")
    admin_client.get(f"/admin/project/{answered_project.id}/export.json")
    assert [e.file_format for e in ExportLog.query.order_by(ExportLog.id)] == ["csv", "json"]
    page = admin_client.get(f"/admin/view-annotation-project/{answered_project.id}")
    assert b"Export history" in page.data and b"Workload per person" in page.data


def test_unknown_format_is_404(admin_client, answered_project):
    assert admin_client.get(f"/admin/project/{answered_project.id}/export.xml").status_code == 404


def test_annotator_cannot_export(user_client, answered_project):
    assert user_client.get(f"/admin/project/{answered_project.id}/export.csv").status_code == 403


def test_create_annotation_project_with_multiple_choice(admin_client):
    admin_client.post("/admin/create-annotation-project", data={
        "project_name": "MC", "project_desc": "d", "answers_per_task": "3", "max_questions_per_user": "50",
        "total_questions": "2",
        "question_1": "Day or night?", "question_type_1": "mcq", "question_options_1": "Day\nNight\n",
        "question_2": "Describe it", "question_type_2": "text",
    })
    project = Project.query.filter_by(name="MC").one()
    mcq, text = project.questions
    assert mcq.answer_type == AnswerType.MCQ and (mcq.number_of_options, mcq.option1, mcq.option2) == (2, "Day", "Night")
    assert text.answer_type == AnswerType.TEXT


def test_multiple_choice_needs_two_to_six_options(admin_client):
    resp = admin_client.post("/admin/create-annotation-project", data={
        "project_name": "Bad", "project_desc": "d", "total_questions": "1",
        "question_1": "Pick", "question_type_1": "mcq", "question_options_1": "Only one",
    }, follow_redirects=True)
    assert b"between 2 and 6 options" in resp.data
    assert Project.query.filter_by(name="Bad").first() is None


def test_annotator_sees_radio_buttons_for_multiple_choice(app, db, answered_project):
    from tests.conftest import login
    from application.models import AnnotationTask

    assignment = (TaskAssignment.query.join(AnnotationTask).join(Question)
                  .filter(Question.answer_type == AnswerType.MCQ).first())
    assignment.is_completed = False
    db.session.commit()
    client = app.test_client()
    login(client, assignment.user.email)
    page = client.get(f"/user/annotation-task/{assignment.id}").data
    assert b'type="radio"' in page and b"Night" in page and b"<textarea" not in page


def test_a_tie_has_no_majority_answer(db, admin_client, answered_project):
    """Two annotators who disagree produce no majority, not the first answer."""
    first_image = ProjectImage.query.filter_by(project_id=answered_project.id).order_by(ProjectImage.id).first()
    tied = next(
        a for a in QuestionAnswer.query.all()
        if a.question.question == "Day or night?" and a.task_assignment.task.image_id == first_image.id
    )
    tied.answer = "Night"
    db.session.commit()

    data = json.loads(admin_client.get(f"/admin/project/{answered_project.id}/export.json").data)
    summaries = {
        img["file_name"]: q["summary"]
        for img in data["images"] for q in img["questions"] if q["question"] == "Day or night?"
    }
    assert summaries[first_image.original_filename]["majority_answer"] is None
    assert summaries[first_image.original_filename]["majority_share"] == 0.5
    assert {s["majority_answer"] for s in summaries.values()} == {None, "Day"}

    rows = csv.DictReader(io.StringIO(admin_client.get(f"/admin/project/{answered_project.id}/export.csv").data.decode()))
    tied_rows = [r for r in rows if r["file_name"] == first_image.original_filename and r["question"] == "Day or night?"]
    assert tied_rows and all(r["task_majority_answer"] == "" for r in tied_rows)
