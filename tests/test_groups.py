from application.models import UserGroup, UserGroupMembership


def test_create_group(admin_client, db):
    resp = admin_client.post(
        "/admin/create-group", data={"group_name": "Class A", "group_desc": "First class"}, follow_redirects=True
    )
    assert b"Group created successfully" in resp.data
    assert UserGroup.query.filter_by(name="Class A").one().description == "First class"


def test_group_names_are_unique(admin_client, db):
    db.session.add(UserGroup(name="Class A"))
    db.session.commit()
    resp = admin_client.post("/admin/create-group", data={"group_name": "Class A"}, follow_redirects=True)
    assert b"already exists" in resp.data
    assert UserGroup.query.count() == 1


def test_add_and_remove_member(admin_client, db, end_user):
    group = UserGroup(name="G")
    db.session.add(group)
    db.session.commit()

    admin_client.post(f"/admin/manage-group/{group.id}", data={"action": "add_user", "user_id": end_user.id})
    assert UserGroupMembership.query.filter_by(group_id=group.id, user_id=end_user.id).count() == 1

    resp = admin_client.post(
        f"/admin/manage-group/{group.id}", data={"action": "add_user", "user_id": end_user.id}, follow_redirects=True
    )
    assert b"already in this group" in resp.data

    admin_client.post(f"/admin/manage-group/{group.id}", data={"action": "remove_user", "user_id": end_user.id})
    assert UserGroupMembership.query.filter_by(group_id=group.id).count() == 0


def test_delete_group_removes_memberships(admin_client, db, end_user):
    group = UserGroup(name="G")
    db.session.add(group)
    db.session.commit()
    db.session.add(UserGroupMembership(user_id=end_user.id, group_id=group.id))
    db.session.commit()

    admin_client.post(f"/admin/delete-group/{group.id}")
    assert UserGroup.query.count() == 0
    assert UserGroupMembership.query.count() == 0


def test_manage_group_page_lists_available_users(admin_client, db, end_user):
    group = UserGroup(name="G")
    db.session.add(group)
    db.session.commit()
    resp = admin_client.get(f"/admin/manage-group/{group.id}")
    assert resp.status_code == 200
    assert end_user.email.encode() in resp.data


def test_removing_member_through_the_ui_redistributes_their_tasks(admin_client, db, make_user):
    from application.models import AnnotationTask, GroupProject, Project, ProjectImage, Question, TaskAssignment
    from application import distribution

    project = Project(name="P", desc="d", is_annotation_project=True, answers_per_task=1, max_questions_per_user=100)
    group = UserGroup(name="G")
    db.session.add_all([project, group])
    db.session.flush()
    db.session.add(Question(question="Q", project_id=project.id))
    db.session.add_all([ProjectImage(project_id=project.id, filename=f"{i}", original_filename=f"{i}", file_path="x") for i in range(4)])
    db.session.add(GroupProject(group_id=group.id, project_id=project.id))
    a, b = make_user(), make_user()
    db.session.add_all([UserGroupMembership(user_id=a.id, group_id=group.id), UserGroupMembership(user_id=b.id, group_id=group.id)])
    db.session.commit()
    distribution.distribute(project)

    resp = admin_client.post(
        f"/admin/manage-group/{group.id}", data={"action": "remove_user", "user_id": a.id}, follow_redirects=True
    )
    assert b"2 unfinished assignments released" in resp.data
    assert TaskAssignment.query.filter_by(user_id=a.id).count() == 0
    assert TaskAssignment.query.filter_by(user_id=b.id).count() == 4

    resp = admin_client.post(
        f"/admin/manage-group/{group.id}", data={"action": "add_user", "user_id": a.id}, follow_redirects=True
    )
    assert b"moved to balance workloads" in resp.data
    assert TaskAssignment.query.filter_by(user_id=a.id).count() == 2


def test_survey_distribution_gives_each_member_each_question_once(db, make_user):
    """Running the survey hand-out twice must not duplicate anyone's questions."""
    from application.groups import distribute_questions_to_group
    from application.models import GroupProject, Project, Question, UserQuestionAssignment

    project = Project(name="Survey", desc="d")
    db.session.add(project)
    db.session.flush()
    db.session.add_all([Question(question="A?", project_id=project.id), Question(question="B?", project_id=project.id)])
    group = UserGroup(name="G")
    db.session.add(group)
    db.session.flush()
    people = [make_user(), make_user()]
    db.session.add_all([UserGroupMembership(user_id=p.id, group_id=group.id) for p in people])
    db.session.add(GroupProject(group_id=group.id, project_id=project.id))
    db.session.commit()

    distribute_questions_to_group(group, project)
    distribute_questions_to_group(group, project)

    rows = UserQuestionAssignment.query.filter_by(group_project_id=f"{group.id}-{project.id}").all()
    assert sorted((r.user_id, r.question_id) for r in rows) == sorted(
        (p.id, q.id) for p in people for q in project.questions)
