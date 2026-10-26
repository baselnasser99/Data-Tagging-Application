"""Tests for the task distribution engine (thesis ch. 3 and 6.5.3).

Each test states one guarantee from application/distribution.py.
"""
from collections import Counter
from contextlib import contextmanager

import pytest
from sqlalchemy import event, update

from application import distribution
from application.models import (
    AnnotationTask,
    AnswerType,
    GroupProject,
    Project,
    ProjectImage,
    Question,
    QuestionAnswer,
    TaskAssignment,
    UserGroup,
    UserGroupMembership,
)


@pytest.fixture(params=[False, True], ids=["per-question", "per-item"])
def setup(request, db, make_user):
    """Build a project with images, questions and a group of annotators.

    Every test using this fixture runs twice: once handing out single questions
    (the default) and once handing out whole items.
    """

    def _setup(n_images=6, n_questions=2, n_users=3, answers_per_task=1, cap=100, per_item=None):
        project = Project(
            name="P", desc="d", is_annotation_project=True,
            answers_per_task=answers_per_task, max_questions_per_user=cap,
            hand_out_per_item=request.param if per_item is None else per_item,
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
    # within one task, or within one image's 3 questions when handing out items
    assert max(load.values()) - min(load.values()) <= (3 if project.hand_out_per_item else 1)
    assert stranded_slots(project) == 0


def test_whoever_gets_an_image_gets_all_its_questions(setup):
    """One sitting per image: an image's questions are not split between people."""
    project, _, _ = setup(n_images=7, n_questions=3, n_users=3, per_item=True)
    distribution.distribute(project)
    owners = {}
    for a in assignments(project):
        owners.setdefault(a.task.image_id, set()).add(a.user_id)
    assert all(len(people) == 1 for people in owners.values())


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
    assert any("Max tasks per person" in w for w in result.warnings)


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
    assert sum(result.moves.values()) == 4  # the message names who gave work to whom


def test_rebalancing_tries_other_pairs_when_partners_share_every_task(db, setup):
    """With 2 answers per task, partners often hold the same tasks. If the least busy
    person already holds all of the busiest person's tasks, the work goes to the next
    least busy person instead of rebalancing stopping with a large gap."""
    project, group, (a, b, c, e) = setup(n_images=17, n_questions=1, n_users=4, answers_per_task=2)
    distribution.generate_tasks(project)
    tasks = AnnotationTask.query.filter_by(project_id=project.id).order_by(AnnotationTask.id).all()
    for task in tasks:  # a and b share tasks 0-8, c and e share 9-16; a and c have finished
        finished, waiting = (a, b) if tasks.index(task) < 9 else (c, e)
        db.session.add(TaskAssignment(task_id=task.id, user_id=finished.id, is_completed=True))
        db.session.add(TaskAssignment(task_id=task.id, user_id=waiting.id))
        task.current_answers = 1
    db.session.commit()
    done_before = {(x.task_id, x.user_id) for x in assignments(project) if x.is_completed}
    assert open_load(project)[b.id] == 9 and open_load(project)[a.id] == 0  # a holds all of b's tasks

    result = distribution.rebalance(project)

    load = open_load(project)
    loads = [load[u.id] for u in (a, b, c, e)]
    assert max(loads) - min(loads) <= 1, loads
    assert result.assignments_moved > 0
    assert {(x.task_id, x.user_id) for x in assignments(project) if x.is_completed} == done_before
    holders = Counter((x.task_id, x.user_id) for x in assignments(project))
    assert max(holders.values()) == 1  # nobody holds a task twice
    assert len(assignments(project)) == 34  # work moved, nothing added or lost


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


# ---- partial work and changed settings ------------------------------------------

def test_leaving_with_an_item_half_answered(db, setup):
    """The answered question stays with the leaver; only the open one moves."""
    project, group, users = setup(n_images=2, n_questions=2, n_users=2, per_item=True)
    distribution.distribute(project)
    leaver, stayer = users
    answered, unanswered = sorted(
        (a for a in assignments(project) if a.user_id == leaver.id), key=lambda a: a.id
    )  # one whole item: two questions
    complete(answered)
    open_task_id = unanswered.task_id

    UserGroupMembership.query.filter_by(user_id=leaver.id, group_id=group.id).delete()
    db.session.commit()
    distribution.on_member_removed(leaver, group.id)

    assert [a for a in assignments(project) if a.user_id == leaver.id] == [answered]
    assert answered.is_completed
    moved = next(a for a in assignments(project) if a.task_id == open_task_id)
    assert moved.user_id == stayer.id and not moved.is_completed
    assert stranded_slots(project) == 0


def test_a_new_question_goes_to_whoever_is_working_on_the_item(db, setup):
    """Per-item mode: all open questions about an item stay with one annotator,
    also when a question is added after work has started."""
    project, _, users = setup(n_images=6, n_questions=3, n_users=3, per_item=True)
    distribution.distribute(project)
    # Everyone answers one question of each of their items, so every item is
    # half done, and loads differ: the first person also finishes one item.
    first_of_item = {}
    for a in sorted(assignments(project), key=lambda a: a.id):
        first_of_item.setdefault(a.task.image_id, a)
    for a in first_of_item.values():
        complete(a)
    finished_item = next(iter(first_of_item))
    for a in assignments(project):
        if a.task.image_id == finished_item and not a.is_completed:
            complete(a)

    db.session.add(Question(question="Added later", project_id=project.id))
    db.session.commit()
    distribution.distribute(project)

    open_holders = {}
    for a in assignments(project):
        if not a.is_completed:
            open_holders.setdefault(a.task.image_id, set()).add(a.user_id)
    assert all(len(people) == 1 for people in open_holders.values()), open_holders
    assert stranded_slots(project) == 0


def test_lowering_answers_per_task_releases_work_no_longer_needed(setup):
    project, _, users = setup(n_images=2, n_questions=1, n_users=3, answers_per_task=3)
    distribution.distribute(project)
    finished = assignments(project)[0]
    complete(finished)

    project.answers_per_task = 1
    result = distribution.apply_answers_per_task(project)

    for task in AnnotationTask.query.filter_by(project_id=project.id):
        assert len(task.assignments) == 1
    # the task that already had an answer keeps it and is now complete
    assert finished.task.assignments == [finished] and finished.task.is_completed
    assert result.assignments_released == 4  # 6 held, 2 still needed


def test_lowering_the_cap_gives_back_unstarted_work(setup):
    project, _, users = setup(n_images=6, n_questions=2, n_users=2, cap=100)
    distribution.distribute(project)  # 12 tasks, 6 each
    finished = sorted((a for a in assignments(project) if a.user_id == users[0].id), key=lambda a: a.id)[:2]
    for a in finished:
        complete(a)

    project.max_questions_per_user = 4
    result = distribution.distribute(project)

    per_user = Counter(a.user_id for a in assignments(project))
    assert per_user == {users[0].id: 4, users[1].id: 4}
    assert all(a in assignments(project) and a.is_completed for a in finished)
    assert result.assignments_released == 4
    assert stranded_slots(project) == 4  # 12 tasks, room for only 8
    assert any("limit" in w for w in result.warnings)


def _edit_questions(admin_client, project, **fields):
    # Same fields as the real "Save All Questions" form: no project settings.
    data = {"action": "update_questions", **fields}
    return admin_client.post(f"/admin/project/{project.id}/edit-questions", data=data, follow_redirects=True)


def test_changing_a_question_type_reopens_its_tasks(admin_client, setup):
    project, _, users = setup(n_images=2, n_questions=1, n_users=2)
    distribution.distribute(project)
    for a in assignments(project):
        complete(a)
    q = project.questions[0]

    resp = _edit_questions(admin_client, project, **{
        f"question-{q.id}": q.question, f"dropdown-{q.id}": "mcq",
        f"confirm_delete_answers_{q.id}": "true", f"number_of_options-{q.id}": "2",
        f"mcq-option-1-{q.id}": "Yes", f"mcq-option-2-{q.id}": "No",
    })

    assert b"will be answered again" in resp.data
    assert q.answer_type == AnswerType.MCQ and (q.option1, q.option2) == ("Yes", "No")
    assert QuestionAnswer.query.filter_by(question_id=q.id).count() == 0
    for task in AnnotationTask.query.filter_by(project_id=project.id):
        assert not task.is_completed and task.current_answers == 0
    # the same people are asked again
    assert len(assignments(project)) == 2 and not any(a.is_completed for a in assignments(project))


def test_rewording_an_answered_question_warns_and_keeps_answers_and_settings(admin_client, setup):
    project, _, users = setup(n_images=1, n_questions=1, n_users=2, answers_per_task=2)
    distribution.distribute(project)
    complete(assignments(project)[0])
    q = project.questions[0]

    # Annotation projects lock the type dropdown, so the browser sends no type.
    resp = _edit_questions(admin_client, project, **{f"question-{q.id}": "A new meaning?"})

    assert b"was reworded" in resp.data
    assert q.question == "A new meaning?"
    assert QuestionAnswer.query.filter_by(question_id=q.id).count() == 1
    # Regression: saving questions used to reset "answers per task" to 1.
    assert project.answers_per_task == 2 and len(assignments(project)) == 2


def test_deleting_a_question_removes_only_its_tasks_and_answers(admin_client, setup):
    project, _, users = setup(n_images=2, n_questions=2, n_users=2)
    distribution.distribute(project)
    for a in assignments(project):
        complete(a)
    gone, kept = project.questions
    gone_id, kept_id = gone.id, kept.id

    admin_client.post(f"/admin/project/{project.id}/edit-questions",
                      data={"action": "delete_question", "question_id_to_delete": gone_id})

    assert AnnotationTask.query.filter_by(question_id=gone_id).count() == 0
    assert QuestionAnswer.query.filter_by(question_id=gone_id).count() == 0
    assert AnnotationTask.query.filter_by(question_id=kept_id).count() == 2
    assert QuestionAnswer.query.filter_by(question_id=kept_id).count() == 2
    assert stranded_slots(project) == 0


# ---- handing out single questions (the thesis default) -----------------------

def _tasks_by_user(project):
    mine = {}
    for a in assignments(project):
        mine.setdefault(a.user_id, []).append(a.task)
    return mine


def test_one_item_two_questions_two_people_get_one_each(setup):
    project, _, users = setup(n_images=1, n_questions=2, n_users=2, per_item=False)
    distribution.distribute(project)
    mine = _tasks_by_user(project)
    assert sorted(len(t) for t in mine.values()) == [1, 1]
    assert {t.question_id for tasks in mine.values() for t in tasks} == {q.id for q in project.questions}


def test_twenty_five_unique_tasks_for_five_people(setup):
    """5 items x 5 questions, 5 people, 1 answer each: 5 unique tasks per person."""
    project, _, users = setup(n_images=5, n_questions=5, n_users=5, per_item=False)
    distribution.distribute(project)
    mine = _tasks_by_user(project)
    assert len(assignments(project)) == 25
    assert len({a.task_id for a in assignments(project)}) == 25  # nobody shares a task
    assert sorted(len(t) for t in mine.values()) == [5] * 5
    # and everyone gets each of the 5 questions once
    for tasks in mine.values():
        assert sorted(Counter(t.question_id for t in tasks).values()) == [1] * 5


def test_everyone_gets_a_mix_of_questions(setup):
    """Regression for the first version: with 4 people and 2 questions, each
    person only ever received one of the two questions."""
    project, _, users = setup(n_images=8, n_questions=2, n_users=4, per_item=False)
    distribution.distribute(project)
    for tasks in _tasks_by_user(project).values():
        assert sorted(Counter(t.question_id for t in tasks).values()) == [2, 2]


def test_uneven_numbers_stay_within_one_task(setup):
    project, _, users = setup(n_images=7, n_questions=1, n_users=3, per_item=False)
    distribution.distribute(project)
    assert sorted(len(t) for t in _tasks_by_user(project).values()) == [2, 2, 3]


def test_projects_from_before_this_setting_keep_handing_out_items(app, db, setup):
    from sqlalchemy import text

    from application import _upgrade_schema

    setup(per_item=False)
    db.session.commit()
    db.session.close()
    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE project DROP COLUMN hand_out_per_item"))
    _upgrade_schema(app)
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT hand_out_per_item FROM project")).scalar_one() in (1, True)


# An annotator can finish a task while a distribution step is running: the step has read
# the work as unfinished, then acts on it a moment later. These tests answer the work in
# that gap, on the same connection, just before the step's own statement reaches the database.

@contextmanager
def answered_just_before(db, statement, assignment_ids):
    """Mark these assignments finished right before the first SQL statement starting with `statement`."""
    fired = []

    def before(conn, cursor, sql, parameters, context, executemany):
        if not fired and sql.lstrip().upper().startswith(statement):
            fired.append(sql)
            for aid in assignment_ids:
                cursor.execute("UPDATE task_assignment SET is_completed = 1 WHERE id = ?", (aid,))

    event.listen(db.engine, "before_cursor_execute", before)
    try:
        yield fired
    finally:
        event.remove(db.engine, "before_cursor_execute", before)
    assert fired, f"no {statement} statement ran"


def test_a_leaver_answering_at_the_last_moment_keeps_the_answer(db, setup):
    project, group, users = setup(n_images=9, n_questions=2, n_users=3)
    distribution.distribute(project)
    leaver = users[0]
    last_answer = [a for a in assignments(project) if a.user_id == leaver.id][0]
    UserGroupMembership.query.filter_by(user_id=leaver.id, group_id=group.id).delete()
    db.session.commit()

    with answered_just_before(db, "DELETE FROM TASK_ASSIGNMENT", [last_answer.id]):
        result = distribution.on_member_removed(leaver, group.id)

    db.session.expire_all()
    kept = db.session.get(TaskAssignment, last_answer.id)
    assert kept is not None and kept.is_completed and kept.user_id == leaver.id
    assert result.assignments_released == 5  # 6 held, 1 answered just in time


def test_lowering_answers_per_task_keeps_an_answer_given_meanwhile(db, setup):
    project, _, users = setup(n_images=2, n_questions=1, n_users=3, answers_per_task=3)
    distribution.distribute(project)
    newest = max(assignments(project), key=lambda a: a.id)  # first in line to be released

    project.answers_per_task = 1
    with answered_just_before(db, "DELETE FROM TASK_ASSIGNMENT", [newest.id]):
        result = distribution.apply_answers_per_task(project)

    db.session.expire_all()
    assert db.session.get(TaskAssignment, newest.id).is_completed
    assert result.assignments_released == 3  # 4 surplus, 1 answered just in time


def test_lowering_the_cap_keeps_an_answer_given_meanwhile(db, setup):
    project, _, users = setup(n_images=6, n_questions=2, n_users=2)
    distribution.distribute(project)  # 12 tasks, 6 each
    newest = max((a for a in assignments(project) if a.user_id == users[0].id), key=lambda a: a.id)

    project.max_questions_per_user = 4
    with answered_just_before(db, "DELETE FROM TASK_ASSIGNMENT", [newest.id]):
        result = distribution.release_over_cap(project)

    db.session.expire_all()
    assert db.session.get(TaskAssignment, newest.id).is_completed
    assert result.assignments_released == 3  # 2 over the cap each, 1 answered just in time


def test_rebalancing_leaves_work_answered_meanwhile_with_whoever_answered_it(db, setup, make_user):
    project, group, (busy,) = setup(n_images=6, n_questions=1, n_users=1)
    distribution.distribute(project)
    first_to_move = max(assignments(project), key=lambda a: a.task_id)  # bundles go largest key first
    newcomer = make_user()
    db.session.add(UserGroupMembership(user_id=newcomer.id, group_id=group.id))
    db.session.commit()

    with answered_just_before(db, "UPDATE TASK_ASSIGNMENT SET USER_ID", [first_to_move.id]):
        result = distribution.rebalance(project)

    db.session.expire_all()
    answered = db.session.get(TaskAssignment, first_to_move.id)
    assert answered.user_id == busy.id and answered.is_completed
    assert open_load(project) == {busy.id: 3, newcomer.id: 2}  # 5 left open, shared as evenly as it goes
    assert result.assignments_moved == 2


def test_an_answer_to_work_just_moved_to_someone_else_is_not_saved(db, setup, make_user):
    from application.annotation import _save_answer

    project, _, (first,) = setup(n_images=1, n_questions=1, n_users=1)
    distribution.distribute(project)
    other = make_user()
    assignment = assignments(project)[0]  # loaded as first's, like the request that shows the task
    db.session.execute(update(TaskAssignment).where(TaskAssignment.id == assignment.id)
                       .values(user_id=other.id).execution_options(synchronize_session=False))

    assert not _save_answer(assignment, "yes")
    db.session.rollback()
    assert QuestionAnswer.query.count() == 0


def test_changing_answers_per_task_reads_all_assignments_in_one_query(db, setup):
    project, _, _ = setup(n_images=20, n_questions=2, n_users=3, answers_per_task=2)
    distribution.distribute(project)
    db.session.expire_all()
    selects = []
    listener = lambda conn, cursor, sql, *rest: selects.append(sql) if sql.lstrip().upper().startswith("SELECT") else None
    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        distribution.apply_answers_per_task(project)
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
    assert len(selects) <= 3, selects  # tasks, their assignments; not one query per task
