"""Group pages beyond the basics: the manager's own group page, survey projects
and the error messages, so every path in application/groups.py is exercised."""
import pytest

from application.models import (
    AnnotationTask,
    GroupProject,
    Project,
    ProjectImage,
    Question,
    QuestionAnswer,
    TaskAssignment,
    UserGroup,
    UserGroupMembership,
    UserQuestionAssignment,
)
from tests.conftest import login


@pytest.fixture
def team(db, make_user):
    """A group with two members."""
    group = UserGroup(name="Team")
    db.session.add(group)
    db.session.commit()
    members = [make_user(name="Ada A"), make_user(name="Ben B")]
    db.session.add_all([UserGroupMembership(user_id=m.id, group_id=group.id) for m in members])
    db.session.commit()
    return group, members


@pytest.fixture
def photo_project(db):
    """1 photo x 2 questions, one answer per task."""
    project = Project(name="Photos", desc="d", is_annotation_project=True, answers_per_task=1)
    db.session.add(project)
    db.session.flush()
    db.session.add_all([Question(question="What?", project_id=project.id),
                        Question(question="Where?", project_id=project.id)])
    db.session.add(ProjectImage(project_id=project.id, filename="a.png", original_filename="a.png", file_path="/x/a.png"))
    db.session.commit()
    return project


@pytest.fixture
def survey(db):
    project = Project(name="Survey", desc="d")
    db.session.add(project)
    db.session.flush()
    db.session.add_all([Question(question="Q1?", project_id=project.id), Question(question="Q2?", project_id=project.id)])
    db.session.commit()
    return project


def _manager_client(app, make_project_admin, project):
    manager = make_project_admin(project)
    client = app.test_client()
    login(client, manager.email)
    return client


# ---- the manager's own group page (/admin/project/<p>/manage-group/<g>) ----

def test_manager_adds_and_removes_members_of_their_project_group(app, db, admin_client, make_user,
                                                                  make_project_admin, team, photo_project):
    group, members = team
    admin_client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": photo_project.id})
    client = _manager_client(app, make_project_admin, photo_project)
    url = f"/admin/project/{photo_project.id}/manage-group/{group.id}"

    newcomer = make_user(name="Cai C")
    page = client.get(url)
    assert page.status_code == 200 and b"Cai C" in page.data  # offered as a new member

    resp = client.post(url, data={"action": "add_user", "user_id": newcomer.id}, follow_redirects=True)
    assert b"added to group successfully" in resp.data
    assert UserGroupMembership.query.filter_by(group_id=group.id).count() == 3

    resp = client.post(url, data={"action": "add_user", "user_id": newcomer.id}, follow_redirects=True)
    assert b"already in this group" in resp.data
    resp = client.post(url, data={"action": "add_user", "user_id": 9999}, follow_redirects=True)
    assert b"User not found" in resp.data

    # Removing a member hands their unfinished tasks to the others.
    leaver = members[0]
    resp = client.post(url, data={"action": "remove_user", "user_id": leaver.id}, follow_redirects=True)
    assert b"handed to the rest of the team" in resp.data
    assert TaskAssignment.query.filter_by(user_id=leaver.id, is_completed=False).count() == 0
    owners = {a.user_id for a in TaskAssignment.query.join(AnnotationTask).filter(AnnotationTask.project_id == photo_project.id)}
    assert owners and leaver.id not in owners

    resp = client.post(url, data={"action": "remove_user", "user_id": leaver.id}, follow_redirects=True)
    assert b"User not found in group" in resp.data


def test_manager_cannot_open_a_group_that_is_not_on_their_project(app, db, make_project_admin, team, photo_project):
    group, _ = team  # never assigned to the project
    client = _manager_client(app, make_project_admin, photo_project)
    resp = client.get(f"/admin/project/{photo_project.id}/manage-group/{group.id}", follow_redirects=True)
    assert b"not associated with your project" in resp.data


# ---- the admin's group page: error messages ----

def test_admin_group_page_reports_unknown_people(admin_client, team):
    group, _ = team
    url = f"/admin/manage-group/{group.id}"
    assert b"User not found" in admin_client.post(url, data={"action": "add_user", "user_id": 9999},
                                                  follow_redirects=True).data
    assert b"User not found in group" in admin_client.post(url, data={"action": "remove_user", "user_id": 9999},
                                                           follow_redirects=True).data


# ---- only active members can join a group ----

@pytest.fixture
def not_members(db, make_user, admin_user):
    """Accounts that the add-member list never offers: an admin, a manager and a removed member."""
    removed = make_user(name="Gone G")
    removed.active = False
    db.session.commit()
    return [admin_user, make_user(role="project-admin"), removed]


def test_manager_cannot_add_admins_managers_or_removed_people_to_their_group(
        app, db, admin_client, make_project_admin, team, photo_project, not_members):
    group, _ = team
    admin_client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": photo_project.id})
    client = _manager_client(app, make_project_admin, photo_project)
    url = f"/admin/project/{photo_project.id}/manage-group/{group.id}"
    for account in not_members:
        resp = client.post(url, data={"action": "add_user", "user_id": account.id}, follow_redirects=True)
        assert b"Only active members can join a group" in resp.data
    assert UserGroupMembership.query.filter_by(group_id=group.id).count() == 2


def test_admin_cannot_add_admins_managers_or_removed_people_to_a_group(admin_client, team, not_members):
    group, _ = team
    for account in not_members:
        admin_client.post(f"/admin/manage-group/{group.id}", data={"action": "add_user", "user_id": account.id})
    assert UserGroupMembership.query.filter_by(group_id=group.id).count() == 2


# ---- survey projects ----

def test_survey_questions_follow_members_in_and_out(admin_client, db, make_user, team, survey):
    group, members = team
    resp = admin_client.post("/admin/assign-project-to-group",
                             data={"group_id": group.id, "project_id": survey.id}, follow_redirects=True)
    assert b"questions distributed successfully" in resp.data
    key = f"{group.id}-{survey.id}"
    assert UserQuestionAssignment.query.filter_by(group_project_id=key).count() == 2 * 2

    # A newcomer gets every survey question.
    newcomer = make_user()
    admin_client.post(f"/admin/manage-group/{group.id}", data={"action": "add_user", "user_id": newcomer.id})
    assert UserQuestionAssignment.query.filter_by(group_project_id=key, user_id=newcomer.id).count() == 2

    # Someone who leaves loses unanswered questions but keeps what they answered.
    leaver = members[0]
    answered = survey.questions[0]
    db.session.add(QuestionAnswer(user_id=leaver.id, question_id=answered.id, answer="Yes"))
    db.session.commit()
    admin_client.post(f"/admin/manage-group/{group.id}", data={"action": "remove_user", "user_id": leaver.id})
    left = UserQuestionAssignment.query.filter_by(group_project_id=key, user_id=leaver.id).all()
    assert [a.question_id for a in left] == [answered.id]

    # Deleting the group removes its survey assignments.
    admin_client.post(f"/admin/delete-group/{group.id}")
    assert UserQuestionAssignment.query.filter_by(group_project_id=key).count() == 0


def test_assigning_projects_without_content_says_what_is_missing(admin_client, db, team):
    group, _ = team
    empty_survey = Project(name="Empty survey", desc="d")
    empty_project = Project(name="Empty project", desc="d", is_annotation_project=True)
    db.session.add_all([empty_survey, empty_project])
    db.session.commit()

    resp = admin_client.post("/admin/assign-project-to-group",
                             data={"group_id": group.id, "project_id": empty_survey.id}, follow_redirects=True)
    assert b"No questions found yet" in resp.data
    resp = admin_client.post("/admin/assign-project-to-group",
                             data={"group_id": group.id, "project_id": empty_project.id}, follow_redirects=True)
    assert b"Upload images and add questions" in resp.data

    resp = admin_client.post("/admin/assign-project-to-group",
                             data={"group_id": group.id, "project_id": empty_survey.id}, follow_redirects=True)
    assert b"already assigned to this group" in resp.data
    resp = admin_client.post("/admin/assign-project-to-group", data={"group_id": 9999, "project_id": 9999},
                             follow_redirects=True)
    assert b"Invalid selection" in resp.data


def test_managers_only_see_their_own_projects_when_assigning(app, db, make_project_admin, photo_project, survey):
    client = _manager_client(app, make_project_admin, photo_project)
    page = client.get("/admin/assign-project-to-group").data
    assert b"Photos (2 questions)" in page and b"Survey (2 questions)" not in page


def test_manager_sees_and_assigns_only_their_own_projects(app, db, admin_client, make_project_admin,
                                                         team, photo_project, survey):
    group, _ = team
    admin_client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": survey.id})
    client = _manager_client(app, make_project_admin, photo_project)

    page = client.get("/admin/assign-project-to-group").data
    assert b"Photos" in page and b"<strong>Survey</strong>" not in page  # no progress of projects they don't run

    resp = client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": survey.id})
    assert resp.status_code == 403
    assert GroupProject.query.filter_by(group_id=group.id).count() == 1  # only the admin's assignment


def test_manager_cannot_change_members_of_a_group_shared_with_other_projects(
        app, db, admin_client, make_user, make_project_admin, team, photo_project, survey):
    group, members = team
    for project in (photo_project, survey):  # the group also works on a survey the manager doesn't run
        admin_client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": project.id})
    client = _manager_client(app, make_project_admin, photo_project)
    url = f"/admin/project/{photo_project.id}/manage-group/{group.id}"

    assert url.encode() in client.get(f"/admin/project/{photo_project.id}/groups").data  # the button leads here
    page = client.get(url).data
    assert b"also works on Survey" in page and b"Add to group" not in page

    newcomer = make_user()
    assert client.post(url, data={"action": "add_user", "user_id": newcomer.id}).status_code == 403
    assert client.post(url, data={"action": "remove_user", "user_id": members[0].id}).status_code == 403
    assert UserGroupMembership.query.filter_by(group_id=group.id).count() == 2  # unchanged


# ---- managers attach only groups that work on nothing but their own projects ----

@pytest.fixture
def others_group(db, admin_client, make_project_admin, team, survey):
    """``team`` (Ada A, Ben B) working on a survey that another manager runs."""
    group, members = team
    make_project_admin(survey)
    admin_client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": survey.id})
    return group


def test_manager_cannot_give_their_project_to_another_managers_group(
        app, make_project_admin, photo_project, others_group):
    client = _manager_client(app, make_project_admin, photo_project)
    resp = client.post("/admin/assign-project-to-group", data={"group_id": others_group.id, "project_id": photo_project.id})
    assert resp.status_code == 403
    assert not GroupProject.query.filter_by(group_id=others_group.id, project_id=photo_project.id).first()


def test_manager_is_not_shown_other_managers_groups_or_their_members(
        app, make_project_admin, photo_project, others_group):
    client = _manager_client(app, make_project_admin, photo_project)
    for url in ("/admin/assign-project-to-group", f"/admin/generate-annotation-tasks/{photo_project.id}",
                "/admin/create-annotation-project"):
        page = client.get(url).data
        assert b"Team (" not in page, url  # not offered: options read "Team (2 members)" or "Team (2 people)"
        assert b"Ada A" not in page, url


def test_manager_cannot_hand_out_tasks_to_another_managers_group(app, make_project_admin, photo_project, others_group):
    client = _manager_client(app, make_project_admin, photo_project)
    resp = client.post(f"/admin/generate-annotation-tasks/{photo_project.id}", data={"group_id": others_group.id})
    assert resp.status_code == 403
    assert not GroupProject.query.filter_by(group_id=others_group.id, project_id=photo_project.id).first()
    assert TaskAssignment.query.count() == 0


def test_new_project_is_not_given_to_another_managers_group(app, make_project_admin, photo_project, others_group):
    client = _manager_client(app, make_project_admin, photo_project)
    resp = client.post("/admin/create-annotation-project", data={
        "project_name": "Mine", "project_desc": "d", "total_questions": "1", "question_1": "Q?",
        "assigned_group_id": others_group.id}, follow_redirects=True)
    mine = Project.query.filter_by(name="Mine").one()  # the project itself is still created
    assert not GroupProject.query.filter_by(group_id=others_group.id, project_id=mine.id).first()
    assert b"ask an admin" in resp.data


def test_shared_group_page_shows_a_manager_how_many_members_not_who(
        app, admin_client, make_project_admin, team, photo_project, others_group):
    # An admin put the group on this manager's project too: they see the size, not names or emails.
    _, members = team
    admin_client.post("/admin/assign-project-to-group", data={"group_id": others_group.id, "project_id": photo_project.id})
    client = _manager_client(app, make_project_admin, photo_project)
    page = client.get(f"/admin/project/{photo_project.id}/manage-group/{others_group.id}").data
    assert b"2 members" in page
    for member in members:
        assert member.full_name.encode() not in page and member.email.encode() not in page


def test_manager_can_still_use_a_group_of_their_own(app, db, make_project_admin, photo_project):
    group = UserGroup(name="Free")
    db.session.add(group)
    db.session.commit()
    client = _manager_client(app, make_project_admin, photo_project)
    assert f'value="{group.id}"'.encode() in client.get("/admin/assign-project-to-group").data
    client.post("/admin/assign-project-to-group", data={"group_id": group.id, "project_id": photo_project.id})
    assert GroupProject.query.filter_by(group_id=group.id, project_id=photo_project.id).first()


def test_groups_page_has_search_and_shows_members_and_projects(admin_client, db, make_user):
    from application.models import GroupProject, Project, UserGroup, UserGroupMembership
    group, empty = UserGroup(name="Radiology"), UserGroup(name="Night shift")
    project = Project(name="Chest scans", desc="d")
    db.session.add_all([group, empty, project])
    db.session.flush()
    member = make_user(name="Zara Quinn")
    db.session.add_all([UserGroupMembership(user_id=member.id, group_id=group.id),
                        GroupProject(group_id=group.id, project_id=project.id)])
    db.session.commit()
    page = admin_client.get("/admin/groups").data
    assert b'data-search-list="group-list"' in page
    assert b'data-search="radiology zara quinn' in page and b"chest scans" in page  # searchable by member and project
    assert b'title="Zara Quinn"' in page and b"No members yet" in page
    assert b"confirm('" not in page  # the group name goes through a data attribute
