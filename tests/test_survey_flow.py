"""Regular (survey-style) projects: manual question creator + group distribution."""
import io

from application.models import (
    AnswerType,
    GroupProject,
    Project,
    QuestionAnswer,
    UserGroup,
    UserGroupMembership,
    UserQuestionAssignment,
)
from tests.conftest import login


def _make_survey(admin_client):
    admin_client.post(
        "/admin/create-project",
        data={
            "project_name": "Opinions",
            "project_desc": "Short survey",
            "total_questions": "2",
            "question-1": "Describe the sound",
            "dropdown-1": "text",
            "question-1-file": (io.BytesIO(b"fake audio"), "clip.mp3"),
            "question-2": "Pick a colour",
            "dropdown-2": "mcq",
            "number_of_options-2": "2",
            "mcq-option-1-2": "Red",
            "mcq-option-2-2": "Blue",
        },
        content_type="multipart/form-data",
    )
    return Project.query.filter_by(name="Opinions").one()


def test_manual_question_creator_supports_text_mcq_and_media(admin_client):
    project = _make_survey(admin_client)
    text_q, mcq_q = project.questions
    assert text_q.answer_type == AnswerType.TEXT and text_q.file_path.endswith("clip.mp3")
    assert mcq_q.answer_type == AnswerType.MCQ and (mcq_q.option1, mcq_q.option2) == ("Red", "Blue")


def test_assign_survey_to_group_and_answer(app, db, admin_client, end_user):
    project = _make_survey(admin_client)
    group = UserGroup(name="Panel")
    db.session.add(group)
    db.session.commit()
    db.session.add(UserGroupMembership(user_id=end_user.id, group_id=group.id))
    db.session.commit()

    resp = admin_client.post(
        "/admin/assign-project-to-group",
        data={"group_id": group.id, "project_id": project.id},
        follow_redirects=True,
    )
    assert b"distributed successfully" in resp.data
    assert GroupProject.query.filter_by(group_id=group.id, project_id=project.id).one().questions_distributed
    assert UserQuestionAssignment.query.filter_by(user_id=end_user.id).count() == 2

    client = app.test_client()
    login(client, end_user.email)
    resp = client.get("/user/my-projects")
    assert b"Opinions" in resp.data

    gp_id = f"{group.id}-{project.id}"
    assert client.get(f"/user/group-project/{gp_id}").status_code == 200
    q1, q2 = project.questions
    client.post(f"/user/group-project/{gp_id}", data={f"answer_{q1.id}": "Birdsong", f"answer_{q2.id}": "Blue"})
    assert {a.answer for a in QuestionAnswer.query.filter_by(user_id=end_user.id)} == {"Birdsong", "Blue"}

    # Re-submitting updates rather than duplicating.
    client.post(f"/user/group-project/{gp_id}", data={f"answer_{q1.id}": "Rain"})
    assert QuestionAnswer.query.filter_by(user_id=end_user.id, question_id=q1.id).one().answer == "Rain"

    # A tampered field name is ignored instead of crashing the page.
    assert client.post(f"/user/group-project/{gp_id}", data={"answer_x_1": "Hack"}).status_code < 500

    resp = admin_client.get(f"/admin/download-files/{project.id}")
    assert b"Rain" in resp.data and b"Blue" in resp.data
    assert b"localhost:5000" not in resp.data

    resp = admin_client.get("/admin/download-full")
    assert b"Opinions" in resp.data


def test_removed_member_can_no_longer_open_or_change_the_survey(app, db, admin_client, end_user):
    project = _make_survey(admin_client)
    group = UserGroup(name="Panel")
    db.session.add(group)
    db.session.commit()
    admin_client.post(f"/admin/manage-group/{group.id}", data={"action": "add_user", "user_id": end_user.id})
    admin_client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": project.id})

    client = app.test_client()
    login(client, end_user.email)
    gp_id = f"{group.id}-{project.id}"
    q1, _q2 = project.questions
    client.post(f"/user/group-project/{gp_id}", data={f"answer_{q1.id}": "Birdsong"})

    admin_client.post(f"/admin/manage-group/{group.id}", data={"action": "remove_user", "user_id": end_user.id})
    # The answered row is kept as history...
    assert UserQuestionAssignment.query.filter_by(user_id=end_user.id, group_project_id=gp_id).count() == 1

    # ...but it no longer opens the survey or changes the answer.
    resp = client.get(f"/user/group-project/{gp_id}")
    assert resp.status_code == 302 and "/user/my-projects" in resp.location
    client.post(f"/user/group-project/{gp_id}", data={f"answer_{q1.id}": "Changed later"})
    assert QuestionAnswer.query.filter_by(user_id=end_user.id, question_id=q1.id).one().answer == "Birdsong"


def test_imported_survey_questions_still_open_without_a_group(app, db, admin_client, end_user):
    project = _make_survey(admin_client)
    key = f"imported-{project.id}"
    db.session.add_all([UserQuestionAssignment(user_id=end_user.id, question_id=q.id, group_project_id=key)
                        for q in project.questions])
    db.session.commit()

    client = app.test_client()
    login(client, end_user.email)
    assert client.get(f"/user/group-project/{key}").status_code == 200


def test_assigning_same_project_twice_is_prevented(db, admin_client):
    project = Project(name="P", desc="d")
    group = UserGroup(name="G")
    db.session.add_all([project, group])
    db.session.commit()
    admin_client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": project.id})
    resp = admin_client.post(
        "/admin/assign-project-to-group",
        data={"group_id": group.id, "project_id": project.id},
        follow_redirects=True,
    )
    assert b"already assigned" in resp.data


def test_end_user_dashboard_and_profile(user_client):
    assert user_client.get("/user/dashboard").status_code == 200
    assert user_client.get("/user/profile").status_code == 200


def test_end_user_change_password(user_client, end_user):
    resp = user_client.post(
        "/user/change-password",
        data={"current_password": "password123", "new_password": "seven77", "confirm_password": "seven77"},
        follow_redirects=True,
    )
    assert b"at least 8 characters" in resp.data
    assert end_user.check_password("password123")

    resp = user_client.post(
        "/user/change-password",
        data={"current_password": "password123", "new_password": "brandnew1", "confirm_password": "brandnew1"},
        follow_redirects=True,
    )
    assert b"Password changed successfully" in resp.data
    assert end_user.check_password("brandnew1")
