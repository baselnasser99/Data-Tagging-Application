"""Project editing, question management and the manager (project-admin) role."""
import io

import pytest

from application.models import (
    AnnotationTask,
    AnswerType,
    Project,
    ProjectAdmin,
    ProjectImage,
    Question,
    QuestionAnswer,
    TaskAssignment,
    User,
    UserGroup,
    UserGroupMembership,
)
from tests.conftest import login


@pytest.fixture
def survey(db):
    project = Project(name="Survey", desc="d")
    db.session.add(project)
    db.session.flush()
    q = Question(question="Original?", project_id=project.id)
    db.session.add(q)
    db.session.commit()
    return project


@pytest.fixture
def annotation_project(db, make_user):
    """Annotation project with 2 images, 1 question and a 2-person group."""
    project = Project(name="Annot", desc="d", is_annotation_project=True, answers_per_task=1)
    db.session.add(project)
    db.session.flush()
    db.session.add(Question(question="Label?", project_id=project.id))
    for i in range(2):
        db.session.add(
            ProjectImage(project_id=project.id, filename=f"{i}.png", original_filename=f"{i}.png", file_path=f"/x/{i}.png")
        )
    group = UserGroup(name="Team")
    db.session.add(group)
    db.session.flush()
    members = [make_user() for _ in range(2)]
    for m in members:
        db.session.add(UserGroupMembership(user_id=m.id, group_id=group.id))
    db.session.commit()
    return project, group, members


# ---- editing ---------------------------------------------------------------

def test_rename_project_and_attach_question_file(admin_client, survey):
    url = f"/admin/project/{survey.id}/edit-questions"
    resp = admin_client.post(url, data={"action": "update_project_settings", "project_name": "Renamed",
                                        "project_desc": "New description"}, follow_redirects=True)
    assert b"updated successfully" in resp.data
    assert survey.name == "Renamed" and survey.desc == "New description"

    q = survey.questions[0]
    admin_client.post(url, data={"action": "update_questions", f"question-{q.id}": q.question,
                                 f"questionfile-{q.id}": (io.BytesIO(b"img"), "pic.png")},
                      content_type="multipart/form-data")
    assert q.file_path.endswith("pic.png")

    # Long names are shortened to fit the 100-character column, keeping the extension.
    admin_client.post(url, data={"action": "update_questions", f"question-{q.id}": q.question,
                                 f"questionfile-{q.id}": (io.BytesIO(b"img"), "a" * 120 + ".png")},
                      content_type="multipart/form-data")
    assert len(q.file_path) <= 100 and q.file_path.endswith("aaa.png")


def test_project_multiple_choice_options_can_be_edited(admin_client, db, annotation_project):
    """The answer-type box used to be disabled for projects, so option edits were silently dropped."""
    project, _, _ = annotation_project
    q = project.questions[0]
    admin_client.post(f"/admin/project/{project.id}/edit-questions", data={
        "action": "update_questions", f"question-{q.id}": q.question, f"dropdown-{q.id}": "mcq",
        f"number_of_options-{q.id}": "2", f"mcq-option-1-{q.id}": "Cat", f"mcq-option-2-{q.id}": "Dog",
    })
    assert (q.answer_type.value, q.number_of_options, q.option1, q.option2) == ("mcq", 2, "Cat", "Dog")


def test_update_question_text_and_switch_to_mcq(admin_client, survey):
    q = survey.questions[0]
    admin_client.post(
        f"/admin/project/{survey.id}/edit-questions",
        data={
            "action": "update_questions",
            f"question-{q.id}": "Updated?",
            f"dropdown-{q.id}": "mcq",
            f"number_of_options-{q.id}": "2",
            f"mcq-option-1-{q.id}": "Yes",
            f"mcq-option-2-{q.id}": "No",
            f"mcq-correct-answer-{q.id}": "Yes",
        },
    )
    assert q.question == "Updated?"
    assert q.answer_type == AnswerType.MCQ
    assert (q.option1, q.option2, q.answer) == ("Yes", "No", "Yes")


def test_changing_type_with_existing_answers_requires_confirmation(db, admin_client, survey, end_user):
    q = survey.questions[0]
    db.session.add(QuestionAnswer(user_id=end_user.id, question_id=q.id, answer="kept"))
    db.session.commit()
    resp = admin_client.post(
        f"/admin/project/{survey.id}/edit-questions",
        data={"action": "update_questions", f"question-{q.id}": q.question, f"dropdown-{q.id}": "mcq"},
        follow_redirects=True,
    )
    assert b"Cannot change question type" in resp.data
    assert q.answer_type == AnswerType.TEXT
    assert QuestionAnswer.query.count() == 1


def test_confirmed_type_change_deletes_answers_and_changes_type(db, admin_client, survey, end_user):
    """Regression: confirming used to delete the answers but keep the old type."""
    q = survey.questions[0]
    db.session.add(QuestionAnswer(user_id=end_user.id, question_id=q.id, answer="old"))
    db.session.commit()
    admin_client.post(
        f"/admin/project/{survey.id}/edit-questions",
        data={"action": "update_questions", f"question-{q.id}": q.question, f"dropdown-{q.id}": "mcq",
              f"confirm_delete_answers_{q.id}": "true", f"number_of_options-{q.id}": "2",
              f"mcq-option-1-{q.id}": "Yes", f"mcq-option-2-{q.id}": "No"},
    )
    assert q.answer_type == AnswerType.MCQ and (q.option1, q.option2) == ("Yes", "No")
    assert QuestionAnswer.query.count() == 0


def test_add_and_delete_question(admin_client, survey):
    admin_client.post(
        f"/admin/project/{survey.id}/edit-questions",
        data={
            "action": "add_question",
            "new_question_text": "Pick one",
            "new_question_type": "mcq",
            "new_question_num_options": "2",
            "new_question_option_1": "A",
            "new_question_option_2": "B",
        },
    )
    new_q = Question.query.filter_by(question="Pick one").one()
    assert new_q.answer_type == AnswerType.MCQ and new_q.option2 == "B"

    admin_client.post(
        f"/admin/project/{survey.id}/edit-questions",
        data={"action": "delete_question", "question_id_to_delete": new_q.id},
    )
    assert Question.query.filter_by(question="Pick one").first() is None


def test_update_annotation_settings(admin_client, annotation_project):
    project, _, _ = annotation_project
    admin_client.post(
        f"/admin/project/{project.id}/edit-questions",
        data={"action": "update_project_settings", "questions_per_image": "3", "max_questions_per_user": "7", "answers_per_task": "2"},
    )
    assert (project.questions_per_image, project.max_questions_per_user, project.answers_per_task) == (3, 7, 2)


# ---- assigning annotation projects ------------------------------------------

def test_assign_annotation_project_to_group_generates_and_distributes_tasks(admin_client, annotation_project):
    project, group, members = annotation_project
    resp = admin_client.post(
        "/admin/assign-project-to-group", data={"group_id": group.id, "project_id": project.id}, follow_redirects=True
    )
    assert b"2 new tasks created" in resp.data and b"2 assignments given out" in resp.data
    assert AnnotationTask.query.filter_by(project_id=project.id).count() == 2
    assert TaskAssignment.query.count() == 2


def test_annotator_sees_annotation_project(app, admin_client, annotation_project):
    project, group, members = annotation_project
    admin_client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": project.id})
    client = app.test_client()
    login(client, members[0].email)
    resp = client.get("/user/my-projects")
    assert b"Annot" in resp.data
    assert client.get("/user/dashboard").status_code == 200


def test_import_users_straight_into_annotation_project(admin_client, annotation_project):
    project, group, members = annotation_project
    admin_client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": project.id})
    csv_data = "full_name,email,password\nNew,new@example.com,secret123\n"
    resp = admin_client.post(
        "/admin/import-users",
        data={"csv_file": (io.BytesIO(csv_data.encode()), "u.csv"), "project_id": str(project.id)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    new_user = User.query.filter_by(email="new@example.com").one()
    assert new_user is not None


def test_import_users_into_survey_project_assigns_questions(admin_client, survey):
    from application.models import UserQuestionAssignment

    csv_data = "full_name,email,password\nNew,new@example.com,secret123\n"
    admin_client.post(
        "/admin/import-users",
        data={"csv_file": (io.BytesIO(csv_data.encode()), "u.csv"), "project_id": str(survey.id)},
        content_type="multipart/form-data",
    )
    new_user = User.query.filter_by(email="new@example.com").one()
    assert UserQuestionAssignment.query.filter_by(user_id=new_user.id).count() == 1


# ---- manager (project-admin) role ------------------------------------------

def test_admin_can_create_a_manager_for_a_project(admin_client, survey):
    resp = admin_client.post(
        f"/admin/project/{survey.id}/admins/create",
        data={"fullname": "Manny", "email": "manny@example.com", "password": "secret123"},
        follow_redirects=True,
    )
    assert b"created successfully" in resp.data
    manager = User.query.filter_by(email="manny@example.com").one()
    assert manager.role == "project-admin" and manager.is_project_admin(survey.id)


def test_creating_a_manager_needs_a_long_enough_password(admin_client, survey):
    resp = admin_client.post(
        f"/admin/project/{survey.id}/admins/create",
        data={"fullname": "Shorty", "email": "shorty@example.com", "password": "short"},
        follow_redirects=True,
    )
    assert b"at least 8 characters" in resp.data
    assert User.query.filter_by(email="shorty@example.com").first() is None


def test_creating_a_manager_is_audited(caplog, admin_client, survey):
    import logging

    caplog.set_level(logging.INFO, logger="audit")
    admin_client.post(
        f"/admin/project/{survey.id}/admins/create",
        data={"fullname": " Manny ", "email": " manny@example.com ", "password": "secret123"},
    )
    manager = User.query.filter_by(email="manny@example.com").one()
    assert manager.full_name == "Manny"
    events = [r.getMessage() for r in caplog.records if r.name == "audit"]
    assert any(e.startswith(f"USER_CREATED user_id={manager.id} role=project-admin") for e in events)


def test_only_one_manager_per_project(admin_client, survey, make_project_admin):
    make_project_admin(survey)
    resp = admin_client.post(
        f"/admin/project/{survey.id}/admins/create",
        data={"fullname": "Second", "email": "second@example.com", "password": "secret123"},
        follow_redirects=True,
    )
    assert b"Only one manager" in resp.data


def test_promote_existing_user_and_remove_again(admin_client, survey, end_user):
    resp = admin_client.post(
        f"/admin/project/{survey.id}/admins/add", data={"email": end_user.email}, follow_redirects=True
    )
    assert b"now manages" in resp.data
    assert end_user.role == "project-admin"

    pa = ProjectAdmin.query.filter_by(user_id=end_user.id).one()
    admin_client.post(f"/admin/project/{survey.id}/admins/{pa.id}/remove")
    assert ProjectAdmin.query.count() == 0
    assert end_user.role == "end-user"  # demoted when no projects remain


def test_add_manager_with_unknown_email(admin_client, survey):
    resp = admin_client.post(
        f"/admin/project/{survey.id}/admins/add", data={"email": "ghost@example.com"}, follow_redirects=True
    )
    assert b"No user with that email was found" in resp.data


def test_manager_cannot_remove_themselves(app, survey, make_project_admin):
    manager = make_project_admin(survey)
    pa = ProjectAdmin.query.filter_by(user_id=manager.id).one()
    client = app.test_client()
    login(client, manager.email)
    resp = client.post(f"/admin/project/{survey.id}/admins/{pa.id}/remove", follow_redirects=True)
    assert b"cannot remove yourself" in resp.data
    assert ProjectAdmin.query.count() == 1


def test_manager_can_create_projects_they_then_manage(app, survey, make_project_admin):
    manager = make_project_admin(survey)
    client = app.test_client()
    login(client, manager.email)
    client.post(
        "/admin/create-annotation-project",
        data={"project_name": "Mine", "project_desc": "d", "total_questions": "1", "question_1": "Q?"},
    )
    mine = Project.query.filter_by(name="Mine").one()
    assert manager.is_project_admin(mine.id)


def test_manager_cannot_edit_another_projects_question_through_their_own_form(app, db, survey, make_project_admin, end_user):
    """Question ids on the edit form must belong to the project in the address."""
    mine = Project(name="Mine", desc="d")
    db.session.add(mine)
    db.session.commit()
    manager = make_project_admin(mine)
    theirs = Question.query.filter_by(project_id=survey.id).one()
    db.session.add(QuestionAnswer(user_id=end_user.id, question_id=theirs.id, answer="kept"))
    db.session.commit()

    client = app.test_client()
    login(client, manager.email)
    client.post(f"/admin/project/{mine.id}/edit-questions", data={
        "action": "update_questions",
        f"question-{theirs.id}": "Hijacked",
        f"dropdown-{theirs.id}": "mcq",
        f"confirm_delete_answers_{theirs.id}": "true",
        f"number_of_options-{theirs.id}": "2",
        f"questionfile-{theirs.id}": (io.BytesIO(b"\x89PNG"), "planted.png"),
    }, content_type="multipart/form-data")

    db.session.expire_all()
    question = db.session.get(Question, theirs.id)
    assert (question.question, question.answer_type, question.file_path) == ("Original?", AnswerType.TEXT, None)
    assert QuestionAnswer.query.filter_by(question_id=theirs.id).count() == 1
    with client.session_transaction() as sess:  # the other project's wording is not shown either
        assert not any("Original?" in message for _cat, message in sess.get("_flashes", []))


# ---- old survey pages outside a group are gone ----------------------------

def test_old_survey_pages_outside_a_group_are_gone(user_client, survey):
    """They let any member read and answer any survey, and download other people's answers."""
    assert user_client.get(f"/user/project/{survey.id}").status_code == 404
    assert user_client.get(f"/user/download-project/{survey.id}").status_code == 404


def test_projects_list_download_gives_annotation_projects_the_csv(admin_client, survey, annotation_project):
    project, _, _ = annotation_project
    page = admin_client.get("/admin/view-projects").data
    assert f"/admin/project/{project.id}/export.csv".encode() in page
    assert f"/admin/download-files/{survey.id}".encode() in page


def test_question_files_must_be_pictures_audio_or_video(admin_client, survey):
    """Attachments are served from the app's own address: an HTML or SVG file could run scripts as the admin."""
    q = survey.questions[0]
    resp = admin_client.post(f"/admin/project/{survey.id}/edit-questions", follow_redirects=True,
                             data={"action": "update_questions", f"question-{q.id}": q.question,
                                   f"questionfile-{q.id}": (io.BytesIO(b"<script>alert(1)</script>"), "evil.html")},
                             content_type="multipart/form-data")
    assert b"was not attached" in resp.data and not (q.file_path or "").endswith("evil.html")

    resp = admin_client.post("/admin/create-project", follow_redirects=True, content_type="multipart/form-data", data={
        "project_name": "Svg test", "project_desc": "d", "total_questions": "1", "question-1": "Q", "dropdown-1": "text",
        "question-1-file": (io.BytesIO(b"<svg onload=alert(1)>"), "x.svg")})
    assert b"was not attached" in resp.data
    assert Project.query.filter_by(name="Svg test").one().questions[0].file_path is None


def test_manager_may_remove_only_managers_not_placed_by_a_system_admin(app, db, survey, make_user, admin_user,
                                                                      make_project_admin):
    me = make_project_admin(survey)
    by_admin, by_me, by_deleted = make_user(role="project-admin"), make_user(role="project-admin"), make_user(role="project-admin")
    rows = {who: ProjectAdmin(user_id=who.id, project_id=survey.id, assigned_by=assigner)
            for who, assigner in ((by_admin, admin_user.id), (by_me, me.id), (by_deleted, 9999))}
    db.session.add_all(rows.values())
    db.session.commit()
    client = app.test_client()
    login(client, me.email)

    resp = client.post(f"/admin/project/{survey.id}/admins/{rows[by_admin].id}/remove", follow_redirects=True)
    assert b"assigned by a system admin" in resp.data
    for who in (by_me, by_deleted):  # placed by me, or by an account that no longer exists
        client.post(f"/admin/project/{survey.id}/admins/{rows[who].id}/remove")
        assert not ProjectAdmin.query.filter_by(user_id=who.id).first()
        assert who.role == "end-user"
    assert ProjectAdmin.query.filter_by(user_id=by_admin.id).first()


@pytest.mark.parametrize("path", ["/admin/create-project", "/admin/create-annotation-project"])
def test_member_made_manager_while_signed_in_manages_the_projects_they_create(path, admin_client, user_client,
                                                                              end_user, survey):
    """The role comes from the account, not from what it was at sign-in."""
    admin_client.post(f"/admin/project/{survey.id}/admins/add", data={"email": end_user.email})
    assert end_user.role == "project-admin"

    user_client.post(path, data={"project_name": "Mine", "project_desc": "d"})
    mine = Project.query.filter_by(name="Mine").one()
    assert ProjectAdmin.query.filter_by(project_id=mine.id, user_id=end_user.id).count() == 1
    assert user_client.get(f"/admin/project/{mine.id}/dashboard").status_code == 200
