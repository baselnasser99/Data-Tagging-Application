"""Admins editing and removing members from the Users page."""
from application import distribution
from application.models import (GroupProject, Project, ProjectImage, Question, TaskAssignment, User,
                                UserGroup, UserGroupMembership)
from tests.conftest import login


def _team(db, make_user, people=2, items=4):
    project = Project(name="P", desc="d", is_annotation_project=True, answers_per_task=1, max_questions_per_user=100)
    group = UserGroup(name="Radiology")
    db.session.add_all([project, group])
    db.session.flush()
    db.session.add(Question(question="Q", project_id=project.id))
    db.session.add_all([ProjectImage(project_id=project.id, filename=f"{i}", original_filename=f"{i}", file_path="x")
                        for i in range(items)])
    db.session.add(GroupProject(group_id=group.id, project_id=project.id))
    users = [make_user() for _ in range(people)]
    db.session.add_all([UserGroupMembership(user_id=u.id, group_id=group.id) for u in users])
    db.session.commit()
    distribution.distribute(project)
    return project, group, users


def test_users_page_lists_people_with_search_and_actions(admin_client, make_user, db):
    _, _, (a, b) = _team(db, make_user)
    loner = make_user(name="Lone Person")
    page = admin_client.get("/admin/users").data
    assert b'id="people-search"' in page and b"Radiology" in page
    assert b"No group" in page and b"Lone Person" in page
    assert f"/admin/users/{a.id}/edit".encode() in page and f"/admin/users/{loner.id}/remove".encode() in page


def test_admin_can_change_name_email_and_password(admin_client, client, make_user, db):
    user = make_user(email="old@example.com")
    resp = admin_client.post(f"/admin/users/{user.id}/edit", follow_redirects=True,
                             data={"full_name": "New Name", "email": "new@example.com", "password": "fresh-pass"})
    assert b"New Name was updated" in resp.data
    db.session.refresh(user)
    assert (user.full_name, user.email) == ("New Name", "new@example.com")
    assert user.check_password("fresh-pass")


def test_editing_keeps_the_password_when_left_empty_and_rejects_a_taken_email(admin_client, make_user, db):
    user, other = make_user(), make_user(email="taken@example.com")
    resp = admin_client.post(f"/admin/users/{user.id}/edit",
                             data={"full_name": "X", "email": "taken@example.com", "password": ""})
    assert b"Someone else already uses taken@example.com" in resp.data
    admin_client.post(f"/admin/users/{user.id}/edit", data={"full_name": "X", "email": user.email, "password": ""})
    db.session.refresh(user)
    assert user.full_name == "X" and user.check_password("password123")


def test_removing_someone_without_answers_deletes_them_and_hands_out_their_tasks(admin_client, make_user, db):
    project, _, (a, b) = _team(db, make_user)
    resp = admin_client.post(f"/admin/users/{a.id}/remove", follow_redirects=True)
    assert b"was deleted" in resp.data
    assert db.session.get(User, a.id) is None
    assert TaskAssignment.query.count() == 4 and {t.user_id for t in TaskAssignment.query} == {b.id}


def test_removing_someone_with_finished_work_keeps_it_and_blocks_sign_in(admin_client, app, make_user, db):
    project, _, (a, b) = _team(db, make_user)
    done = TaskAssignment.query.filter_by(user_id=a.id).first()
    done.is_completed = True
    db.session.commit()

    resp = admin_client.post(f"/admin/users/{a.id}/remove", follow_redirects=True)
    assert b"can no longer sign in" in resp.data
    db.session.refresh(a)
    assert a.active is False and UserGroupMembership.query.filter_by(user_id=a.id).count() == 0
    assert TaskAssignment.query.filter_by(user_id=a.id).all() == [done]  # finished work kept, open work handed on
    assert TaskAssignment.query.count() == 4

    other = app.test_client()
    resp = login(other, a.email, follow=True)
    assert b"incorrect" in resp.data

    admin_client.post(f"/admin/users/{a.id}/restore")
    db.session.refresh(a)
    assert a.active is True


def test_a_removed_person_who_is_signed_in_is_signed_out(app, admin_client, make_user, db):
    user = make_user()
    member = app.test_client()
    login(member, user.email)
    assert member.get("/user/dashboard").status_code == 200
    user.active = False
    db.session.commit()
    assert member.get("/user/dashboard").status_code == 302


def test_only_members_can_be_removed_and_only_by_admins(admin_client, admin_user, app, make_user, db):
    manager = make_user(role="project-admin")
    assert admin_client.post(f"/admin/users/{manager.id}/remove").status_code == 404
    assert admin_client.get(f"/admin/users/{admin_user.id}/edit").status_code == 404  # admins use their profile page
    member = make_user()
    other = app.test_client()
    login(other, manager.email)
    assert other.post(f"/admin/users/{member.id}/remove").status_code == 403
    assert db.session.get(User, member.id) is not None


def test_names_in_confirm_dialogs_cannot_break_out_into_javascript(admin_client, make_user):
    make_user(name="O'Brien'); alert(1); ('")
    page = admin_client.get("/admin/users").data
    assert b"confirm('" not in page and b"O&#39;Brien" in page


def test_managers_page_lists_managers_with_their_projects_search_and_edit(admin_client, make_user, db):
    from application.models import ProjectAdmin
    admin = User.query.filter_by(role="admin").first()
    busy, idle = make_user(role="project-admin", name="Busy Manager"), make_user(role="project-admin", name="Idle Manager")
    project = Project(name="Scans", desc="d")
    db.session.add(project)
    db.session.flush()
    db.session.add(ProjectAdmin(user_id=busy.id, project_id=project.id, assigned_by=admin.id))
    db.session.commit()
    page = admin_client.get("/admin/managers").data
    assert b'data-search-list="manager-list"' in page and b'data-search-list="project-list"' in page
    assert b"Busy Manager" in page and b"No project" in page
    assert f"/admin/users/{idle.id}/edit".encode() in page

    resp = admin_client.post(f"/admin/users/{busy.id}/edit", follow_redirects=True,
                             data={"full_name": "Renamed Manager", "email": busy.email, "password": ""})
    assert b"Renamed Manager was updated" in resp.data and b"Projects and their managers" in resp.data


def test_users_rows_link_the_answer_count_to_the_download(admin_client, make_user, db):
    from application.models import QuestionAnswer
    project, _, (a, b) = _team(db, make_user)
    db.session.add(QuestionAnswer(user_id=a.id, question_id=project.questions[0].id, answer="cat"))
    db.session.commit()
    page = admin_client.get("/admin/users").data
    assert f'href="/admin/download-answers/{a.id}"'.encode() in page
    assert b"<strong>1</strong> answer <i" in page  # the count itself is the download link


def test_refused_pages_and_account_changes_are_audited(caplog, admin_client, user_client):
    import logging

    caplog.set_level(logging.INFO, logger="audit")
    assert user_client.get("/admin/users").status_code == 403
    admin_client.post("/admin/create-user", data={"full_name": "Dee", "email": "dee@example.com", "password": "password123"})
    events = [r.getMessage() for r in caplog.records if r.name == "audit"]
    assert any(e.startswith("ACCESS_DENIED") and "path=/admin/users" in e for e in events)
    assert any(e.startswith("USER_CREATED") for e in events)


def test_people_page_counts_projects_reached_every_way(admin_client, db, make_user):
    """A person's project count joins survey questions, groups and tasks, counting each project once."""
    from flask import template_rendered

    from application.models import (AnnotationTask, GroupProject, Project, ProjectImage, Question,
                                    QuestionAnswer, TaskAssignment, UserGroup, UserGroupMembership,
                                    UserQuestionAssignment)

    busy, idle = make_user(name="Busy"), make_user(name="Idle")
    survey, via_group, labelled, both = (Project(name=n, desc="") for n in ("S", "G", "L", "B"))
    db.session.add_all([survey, via_group, labelled, both])
    db.session.flush()
    survey_q = Question(question="Why?", project_id=survey.id)
    both_q = Question(question="What?", project_id=both.id)
    image = ProjectImage(project_id=labelled.id, filename="a.png", original_filename="a.png", file_path="a.png")
    group_a, group_b = UserGroup(name="A"), UserGroup(name="B")
    db.session.add_all([survey_q, both_q, image, group_a, group_b])
    db.session.flush()
    task = AnnotationTask(project_id=labelled.id, image_id=image.id, question_id=both_q.id)
    db.session.add(task)
    db.session.flush()
    db.session.add_all([
        UserQuestionAssignment(user_id=busy.id, question_id=survey_q.id, group_project_id="imported"),
        UserQuestionAssignment(user_id=busy.id, question_id=both_q.id, group_project_id="imported"),
        UserGroupMembership(user_id=busy.id, group_id=group_b.id),
        UserGroupMembership(user_id=busy.id, group_id=group_a.id),
        GroupProject(group_id=group_a.id, project_id=via_group.id),
        GroupProject(group_id=group_b.id, project_id=both.id),
        TaskAssignment(task_id=task.id, user_id=busy.id),
        QuestionAnswer(user_id=busy.id, question_id=survey_q.id, answer="x"),
        QuestionAnswer(user_id=busy.id, question_id=both_q.id, answer="y"),
    ])
    db.session.commit()

    seen = []

    def record(sender, template, context, **extra):
        seen.append(context)

    with template_rendered.connected_to(record, admin_client.application):
        assert admin_client.get("/admin/users").status_code == 200
    rows = {d["user"].full_name: d for d in seen[0]["users_data"]}
    assert rows["Busy"]["assigned_projects_count"] == 4
    assert rows["Busy"]["answer_count"] == 2
    assert sorted(g.name for g in rows["Busy"]["assigned_groups"]) == ["A", "B"]
    assert (rows["Idle"]["assigned_projects_count"], rows["Idle"]["answer_count"], rows["Idle"]["assigned_groups"]) == (0, 0, [])
