"""Tests for the task distribution engine (thesis ch. 3 and 6.5.3).

Each test states one guarantee from application/distribution.py.
"""
from collections import Counter

import pytest

from application import distribution
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
)


@pytest.fixture
def setup(db, make_user):
    """Build a project with images, questions and a group of annotators."""

    def _setup(n_images=6, n_questions=2, n_users=3, answers_per_task=1, cap=100):
        project = Project(
            name="P", desc="d", is_annotation_project=True,
            answers_per_task=answers_per_task, max_questions_per_user=cap,
        )
        group = UserGroup(name=f"G{n_users}-{n_images}")
        db.session.add_all([project, group])
        db.session.flush()
        db.session.add_all([Question(question=f"Q{i}", project_id=project.id) for i in range(n_questions)])
        db.session.add_all([
            ProjectImage(project_id=project.id, filename=f"{i}.png", original_filename=f"{i}.png", file_path=f"/x/{i}.png")
            for i in range(n_images)
        ])
        db.session.add(GroupProject(group_id=group.id, project_id=project.id))
        users = [make_user() for _ in range(n_users)]
        for u in users:
            db.session.add(UserGroupMembership(user_id=u.id, group_id=group.id))
        db.session.commit()
        return project, group, users

    return _setup


def assignments(project):
    return TaskAssignment.query.join(AnnotationTask).filter(AnnotationTask.project_id == project.id).all()


def open_load(project):
    return Counter(a.user_id for a in assignments(project) if not a.is_completed)


def complete(assignment, answer="done"):
    """Simulate an annotator answering a task."""
    from application.extns import db

    assignment.is_completed = True
    assignment.task.current_answers += 1
    assignment.task.is_completed = assignment.task.current_answers >= assignment.task.required_answers
    db.session.add(QuestionAnswer(
        user_id=assignment.user_id, question_id=assignment.task.question_id,
        answer=answer, task_assignment_id=assignment.id,
    ))
    db.session.commit()


def stranded_slots(project):
    """Open answer slots that nobody holds."""
    missing = 0
    for task in AnnotationTask.query.filter_by(project_id=project.id):
        missing += max(0, task.required_answers - len(task.assignments))
    return missing


# ---- generation -------------------------------------------------------------

def test_generation_creates_one_task_per_image_question_pair(setup):
    project, _, _ = setup(n_images=4, n_questions=3)
    result = distribution.generate_tasks(project)
    assert result.tasks_created == 12
    pairs = {(t.image_id, t.question_id) for t in AnnotationTask.query.filter_by(project_id=project.id)}
    assert len(pairs) == 12


def test_regenerating_keeps_existing_tasks_and_answers(db, setup):
    project, _, _ = setup(n_images=2, n_questions=1)
    distribution.distribute(project)
    first = assignments(project)[0]
    complete(first, "kept answer")

    db.session.add(Question(question="New question", project_id=project.id))
    db.session.commit()
    result = distribution.distribute(project)

    assert result.tasks_created == 2  # only the new question's tasks
    assert AnnotationTask.query.filter_by(project_id=project.id).count() == 4
    assert db.session.get(TaskAssignment, first.id).is_completed
    assert QuestionAnswer.query.filter_by(answer="kept answer").count() == 1


# ---- assignment -------------------------------------------------------------

def test_every_slot_is_filled_and_load_is_even(setup):
    project, _, users = setup(n_images=10, n_questions=3, n_users=4)
    distribution.distribute(project)
    load = open_load(project)
    assert sum(load.values()) == 30
    assert max(load.values()) - min(load.values()) <= 1
    assert stranded_slots(project) == 0


def test_no_user_gets_the_same_task_twice(setup):
    project, _, _ = setup(n_images=5, n_questions=2, n_users=4, answers_per_task=3)
    distribution.distribute(project)
    for task in AnnotationTask.query.filter_by(project_id=project.id):
        holders = [a.user_id for a in task.assignments]
        assert len(holders) == 3 and len(set(holders)) == 3


def test_cap_is_respected_and_reported(setup):
    project, _, _ = setup(n_images=10, n_questions=1, n_users=2, cap=3)
    result = distribution.distribute(project)
    assert all(v <= 3 for v in Counter(a.user_id for a in assignments(project)).values())
    assert result.unfilled_slots == 4
    assert any("Max tasks per user" in w for w in result.warnings)


def test_small_group_warning(setup):
    project, _, _ = setup(n_images=2, n_questions=1, n_users=2, answers_per_task=5)
    result = distribution.distribute(project)
    assert any("asks for 5 answers per task, but only 2 people" in w for w in result.warnings)


def test_running_twice_creates_nothing_new(setup):
    project, _, _ = setup()
    distribution.distribute(project)
    again = distribution.distribute(project)
    assert again.tasks_created == 0 and again.assignments_created == 0 and again.assignments_moved == 0


def test_results_are_reproducible(db, setup, make_user):
    """Same input, same assignment: ties are broken by user id, not randomly."""
    project, _, _ = setup(n_images=4, n_questions=2, n_users=3)
    distribution.distribute(project)
    snapshot = sorted((a.task_id, a.user_id) for a in assignments(project))
    TaskAssignment.query.delete()
    db.session.commit()
    distribution.distribute(project)
    assert sorted((a.task_id, a.user_id) for a in assignments(project)) == snapshot


# ---- redistribution: the thesis's main contribution --------------------------

def test_leaving_member_work_goes_to_the_rest_of_the_team(db, setup):
    project, group, users = setup(n_images=9, n_questions=2, n_users=3)
    distribution.distribute(project)
    leaver = users[0]
    finished = [a for a in assignments(project) if a.user_id == leaver.id][:2]
    for a in finished:
        complete(a)

    UserGroupMembership.query.filter_by(user_id=leaver.id, group_id=group.id).delete()
    db.session.commit()
    result = distribution.on_member_removed(leaver, group.id)

    remaining = assignments(project)
    # nothing unfinished is left with the leaver...
    assert not [a for a in remaining if a.user_id == leaver.id and not a.is_completed]
    # ...their finished work is kept...
    assert sum(1 for a in remaining if a.user_id == leaver.id and a.is_completed) == 2
    # ...no task is left without an owner...
    assert stranded_slots(project) == 0
    # ...and the released work was shared fairly
    load = open_load(project)
    assert set(load) == {users[1].id, users[2].id}
    assert max(load.values()) - min(load.values()) <= 1
    assert result.assignments_released == 4  # 6 held, 2 finished


def test_member_in_two_groups_keeps_their_work(db, setup, make_user):
    project, group, users = setup(n_images=4, n_questions=1, n_users=2)
    other = UserGroup(name="Other")
    db.session.add(other)
    db.session.flush()
    db.session.add(GroupProject(group_id=other.id, project_id=project.id))
    db.session.add(UserGroupMembership(user_id=users[0].id, group_id=other.id))
    db.session.commit()
    distribution.distribute(project)
    before = sorted((a.task_id, a.user_id) for a in assignments(project))

    UserGroupMembership.query.filter_by(user_id=users[0].id, group_id=group.id).delete()
    db.session.commit()
    result = distribution.on_member_removed(users[0], group.id)

    assert result.assignments_released == 0
    assert sorted((a.task_id, a.user_id) for a in assignments(project)) == before


def test_new_member_takes_a_fair_share(db, setup, make_user):
    project, group, users = setup(n_images=12, n_questions=1, n_users=2)
    distribution.distribute(project)
    newcomer = make_user()
    db.session.add(UserGroupMembership(user_id=newcomer.id, group_id=group.id))
    db.session.commit()

    result = distribution.on_member_added(newcomer, group.id)
    load = open_load(project)
    assert load[newcomer.id] == 4  # 12 tasks / 3 people
    assert max(load.values()) - min(load.values()) <= 1
    assert result.assignments_moved == 4


def test_rebalancing_never_moves_finished_work(db, setup, make_user):
    project, group, users = setup(n_images=6, n_questions=1, n_users=1)
    distribution.distribute(project)
    for a in assignments(project)[:4]:
        complete(a)
    newcomer = make_user()
    db.session.add(UserGroupMembership(user_id=newcomer.id, group_id=group.id))
    db.session.commit()
    distribution.on_member_added(newcomer, group.id)

    done = [a for a in assignments(project) if a.is_completed]
    assert len(done) == 4 and all(a.user_id == users[0].id for a in done)
    assert open_load(project)[newcomer.id] == 1  # open work: 2 -> 1 each


def test_deleting_a_group_releases_its_members_work(db, setup, make_user):
    project, group, users = setup(n_images=4, n_questions=1, n_users=2)
    backup = UserGroup(name="Backup")
    db.session.add(backup)
    db.session.flush()
    helper = make_user()
    db.session.add_all([
        GroupProject(group_id=backup.id, project_id=project.id),
        UserGroupMembership(user_id=helper.id, group_id=backup.id),
    ])
    db.session.commit()
    distribution.distribute(project)

    member_ids = [u.id for u in users]
    db.session.delete(group)
    db.session.commit()
    distribution.on_group_detached(group.id, [project.id], member_ids)

    assert all(a.user_id == helper.id for a in assignments(project) if not a.is_completed)
    assert stranded_slots(project) == 0


def test_raising_answers_per_task_adds_slots(setup):
    project, _, users = setup(n_images=3, n_questions=1, n_users=3, answers_per_task=1)
    distribution.distribute(project)
    project.answers_per_task = 2
    distribution.apply_answers_per_task(project)
    distribution.distribute(project)
    for task in AnnotationTask.query.filter_by(project_id=project.id):
        assert task.required_answers == 2 and len(task.assignments) == 2


def test_workload_summary(setup):
    project, _, users = setup(n_images=3, n_questions=1, n_users=3)
    distribution.distribute(project)
    complete(assignments(project)[0])
    summary = {row["user"].id: row for row in distribution.workload_summary(project)}
    assert sum(r["total"] for r in summary.values()) == 3
    assert sum(r["completed"] for r in summary.values()) == 1
