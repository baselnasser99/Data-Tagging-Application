"""Task distribution and redistribution for annotation projects.

This module implements the algorithm described in thesis chapters 3 and 6.5.3.

Vocabulary
    task        one (image, question) pair that needs ``required_answers`` answers
    assignment  one task given to one annotator (a TaskAssignment row)
    eligible    a user who belongs to at least one group assigned to the project
    load        a user's number of *open* (unfinished) assignments in a project

Guarantees, each covered by tests in tests/test_distribution.py:
    1. Uniqueness: a user never receives the same task twice.
    2. Cap: nobody holds more than ``max_questions_per_user`` assignments in a project.
    3. Fairness: work goes to the eligible user with the lowest load, so loads
       stay within one task of each other. Ties go to whoever has answered that
       question least, so everyone gets a mix of questions, then by total
       assignments, then user id, so results are reproducible.
    3b. Per-item projects (``hand_out_per_item``): whoever gets an item gets all
       of its open questions, in one sitting; loads then stay within one item's
       worth of questions.
    4. Completeness: when someone leaves, their unfinished assignments are released
       and immediately offered to the remaining members; finished work is never touched.
    5. Integrity: regenerating tasks only adds missing (image, question) pairs;
       existing tasks, assignments and answers are kept.
    6. Settings changes: lowering ``answers_per_task`` or ``max_questions_per_user``
       releases unfinished assignments that are no longer needed or allowed, and
       hands them to others where possible; finished work is never removed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import case, func, insert
from sqlalchemy.orm import joinedload

from .extns import db
from .models import (
    AnnotationTask,
    GroupProject,
    Project,
    ProjectImage,
    Question,
    QuestionAnswer,
    TaskAssignment,
    User,
    UserGroupMembership,
)


@dataclass
class DistributionResult:
    """Summary of one distribution run, used for messages and tests."""

    tasks_created: int = 0
    assignments_created: int = 0
    assignments_released: int = 0
    assignments_moved: int = 0
    unfilled_slots: int = 0
    warnings: list[str] = field(default_factory=list)
    moves: Counter = field(default_factory=Counter)  # (from name, to name) -> assignments moved

    def merge(self, other: "DistributionResult") -> "DistributionResult":
        self.tasks_created += other.tasks_created
        self.assignments_created += other.assignments_created
        self.assignments_released += other.assignments_released
        self.assignments_moved += other.assignments_moved
        self.unfilled_slots += other.unfilled_slots
        self.moves.update(other.moves)
        self.warnings.extend(w for w in other.warnings if w not in self.warnings)
        return self


# --------------------------------------------------------------------------
# Who can work on a project
# --------------------------------------------------------------------------

def eligible_users(project: Project) -> list[User]:
    """Members of every group assigned to the project, without duplicates."""
    rows = (
        db.session.query(User)
        .join(UserGroupMembership, UserGroupMembership.user_id == User.id)
        .join(GroupProject, GroupProject.group_id == UserGroupMembership.group_id)
        .filter(GroupProject.project_id == project.id)
        .distinct()
        .order_by(User.id)
        .all()
    )
    return rows


def _project_assignments(project_id: int):
    return TaskAssignment.query.join(AnnotationTask).filter(AnnotationTask.project_id == project_id)


def user_loads(project: Project, users: list[User]) -> dict[int, tuple[int, int]]:
    """Map user id -> (open assignments, total assignments) within the project."""
    ids = [u.id for u in users]
    loads = {uid: (0, 0) for uid in ids}
    if not ids:
        return loads
    rows = (
        db.session.query(
            TaskAssignment.user_id,
            func.count(TaskAssignment.id),
            func.sum(case((TaskAssignment.is_completed.is_(False), 1), else_=0)),
        )
        .join(AnnotationTask)
        .filter(AnnotationTask.project_id == project.id, TaskAssignment.user_id.in_(ids))
        .group_by(TaskAssignment.user_id)
        .all()
    )
    for uid, total, open_ in rows:
        loads[uid] = (int(open_ or 0), int(total or 0))
    return loads


# --------------------------------------------------------------------------
# Task generation
# --------------------------------------------------------------------------

def generate_tasks(project: Project) -> DistributionResult:
    """Create a task for every (image, question) pair that does not have one yet.

    Existing tasks are left alone, so completed answers survive when questions
    or images are added later.
    """
    result = DistributionResult()
    images = ProjectImage.query.filter_by(project_id=project.id).all()
    questions = Question.query.filter_by(project_id=project.id).all()
    existing = {
        (t.image_id, t.question_id)
        for t in AnnotationTask.query.filter_by(project_id=project.id).all()
    }
    rows = [
        dict(project_id=project.id, image_id=image.id, question_id=question.id,
             required_answers=project.answers_per_task or 1, current_answers=0, is_completed=False)
        for image in images for question in questions
        if (image.id, question.id) not in existing
    ]
    if rows:
        # One batched INSERT: adding ORM objects one by one costs a database round trip per task.
        db.session.execute(insert(AnnotationTask), rows)
    result.tasks_created = len(rows)
    db.session.commit()
    return result


def apply_answers_per_task(project: Project) -> DistributionResult:
    """Propagate a changed ``answers_per_task`` setting to existing tasks.

    When the setting goes down, a task can hold more people than it now needs;
    their unfinished assignments are released so nobody answers work that is
    already covered. Finished answers are always kept.
    """
    result = DistributionResult()
    for task in AnnotationTask.query.filter_by(project_id=project.id).all():
        task.required_answers = project.answers_per_task or 1
        task.is_completed = task.current_answers >= task.required_answers
        surplus = len(task.assignments) - task.required_answers
        unfinished = sorted((a for a in task.assignments if not a.is_completed), key=lambda a: -a.id)
        for assignment in unfinished[:max(0, surplus)]:
            db.session.delete(assignment)
            result.assignments_released += 1
    db.session.commit()
    return result


def reopen_question(question: Question) -> None:
    """Delete a question's answers and reopen its tasks, so they are asked again.

    Used when a question's type changes: old answers no longer fit it. The same
    people keep the tasks. The caller commits.
    """
    QuestionAnswer.query.filter_by(question_id=question.id).delete()
    for task in AnnotationTask.query.filter_by(question_id=question.id).all():
        task.current_answers = 0
        task.is_completed = False
        for assignment in task.assignments:
            assignment.is_completed = False
            assignment.completed_date = None


# --------------------------------------------------------------------------
# Assignment
# --------------------------------------------------------------------------

def assign_open_slots(project: Project, users: list[User] | None = None) -> DistributionResult:
    """Fill every task's missing answer slots with the least-loaded eligible users.

    By default each task is handed out on its own. In per-item projects a person
    who receives an item gets every question for it that still needs an answer,
    so they answer all of it in one sitting. Work with the most missing answers
    is served first; within an item, the starting question rotates from item to
    item so nobody always ends up with the same question.
    """
    result = DistributionResult()
    users = eligible_users(project) if users is None else users
    if not users:
        return result

    cap = project.max_questions_per_user or 0
    loads = user_loads(project, users)
    open_load = {u.id: loads[u.id][0] for u in users}
    total = {u.id: loads[u.id][1] for u in users}

    tasks = AnnotationTask.query.filter_by(project_id=project.id).order_by(AnnotationTask.id).all()
    holders: dict[int, set[int]] = {t.id: set() for t in tasks}
    seen: Counter = Counter()  # (user, question) -> times that user got that question
    for task_id, user_id, question_id in (
        db.session.query(TaskAssignment.task_id, TaskAssignment.user_id, AnnotationTask.question_id)
        .join(AnnotationTask)
        .filter(AnnotationTask.project_id == project.id)
        .all()
    ):
        holders[task_id].add(user_id)
        seen[user_id, question_id] += 1

    def missing(task):
        return max(0, (task.required_answers or 1) - len(holders[task.id]))

    per_item = project.hand_out_per_item
    units: dict[int, list] = {}  # what one person receives at once: an item, or a single task
    for task in tasks:
        units.setdefault(task.image_id if per_item else task.id, []).append(task)

    def unit_missing(unit_tasks):
        return sum(missing(t) for t in unit_tasks)

    item_index = {i: n for n, i in enumerate(sorted({t.image_id for t in tasks}))}
    question_index = {q: n for n, q in enumerate(sorted({t.question_id for t in tasks}))}

    def unit_order(key):
        first = units[key][0]
        item = item_index[first.image_id]
        # Rotate each item's starting question, so the last person in a round
        # does not always receive the same question.
        rotated = (question_index[first.question_id] - item) % max(1, len(question_index))
        return (-unit_missing(units[key]), item, rotated)

    new_assignments = []
    for key in sorted(units, key=unit_order):
        unit_tasks = units[key]
        while unit_missing(unit_tasks):
            # People who still lack at least one open task of this unit and
            # have room under the cap.
            candidates = [
                u.id for u in users
                if total[u.id] < cap
                and any(missing(t) and u.id not in holders[t.id] for t in unit_tasks)
            ]
            if not candidates:
                result.unfilled_slots += unit_missing(unit_tasks)
                break
            if per_item:
                uid = min(candidates, key=lambda i: (open_load[i], total[i], i))
            else:
                question_id = unit_tasks[0].question_id
                uid = min(candidates, key=lambda i: (open_load[i], seen[i, question_id], total[i], i))
            for task in unit_tasks:
                if missing(task) and uid not in holders[task.id] and total[uid] < cap:
                    new_assignments.append(dict(task_id=task.id, user_id=uid))
                    holders[task.id].add(uid)
                    seen[uid, task.question_id] += 1
                    open_load[uid] += 1
                    total[uid] += 1
                    result.assignments_created += 1

    if new_assignments:
        db.session.execute(insert(TaskAssignment), new_assignments)  # one batch, see generate_tasks
    db.session.commit()

    if result.unfilled_slots:
        result.warnings.append(_shortage_warning(project, len(users)))
    return result


def _shortage_warning(project: Project, n_users: int) -> str:
    required = project.answers_per_task or 1
    if n_users < required:
        return (
            f"'{project.name}' asks for {required} answers per task, but only {n_users} "
            f"{'person is' if n_users == 1 else 'people are'} assigned. Each task can get at most "
            f"{n_users} answers, so tasks will not be marked complete. Lower 'Answers per task' "
            "or add more people."
        )
    return (
        f"Some tasks in '{project.name}' could not be given to anyone because every eligible "
        "person has reached the 'Max tasks per person' limit. Raise the limit or add more people."
    )


def rebalance(project: Project, users: list[User] | None = None) -> DistributionResult:
    """Move unstarted work from the busiest person to the least busy one.

    Work moves one task at a time, or in per-item projects one item at a time
    (all of that person's unstarted questions for it, keeping "one item, one
    sitting"). A move is made only if it narrows the gap between the two people,
    the receiver holds none of those tasks yet, and stays under the cap.
    Completed assignments are never moved. Stops when no move narrows the gap.

    The project's assignments are read once and the moves are worked out in
    memory, then saved together: re-reading every workload after each single
    move made adding one person to a large project take close to a minute.
    """
    result = DistributionResult()
    users = eligible_users(project) if users is None else users
    if len(users) < 2:
        return result
    cap = project.max_questions_per_user or 0
    ids = {u.id for u in users}
    names = {u.id: u.full_name for u in users}

    open_work = {uid: [] for uid in ids}  # each person's unfinished assignments
    held = {uid: set() for uid in ids}    # every task each person holds, finished or not
    total = Counter()
    for assignment in _project_assignments(project.id).options(joinedload(TaskAssignment.task)):
        if assignment.user_id not in ids:
            continue
        held[assignment.user_id].add(assignment.task_id)
        total[assignment.user_id] += 1
        if not assignment.is_completed:
            open_work[assignment.user_id].append(assignment)

    while True:
        by_open = sorted(ids, key=lambda uid: (len(open_work[uid]), uid))
        light, heavy = by_open[0], by_open[-1]
        gap = len(open_work[heavy]) - len(open_work[light])
        if gap <= 1:
            break

        bundles: dict[int, list] = {}
        for assignment in open_work[heavy]:
            key = assignment.task.image_id if project.hand_out_per_item else assignment.task_id
            bundles.setdefault(key, []).append(assignment)

        move = None
        for key in sorted(bundles, key=lambda k: (len(bundles[k]), -k)):
            bundle = bundles[key]
            size = len(bundle)
            if size >= gap:  # moving it would not narrow the gap
                continue
            if any(a.task_id in held[light] for a in bundle):
                continue
            if total[light] + size > cap:
                continue
            move = bundle
            break
        if move is None:
            break
        for assignment in move:
            assignment.user_id = light
            open_work[heavy].remove(assignment)
            open_work[light].append(assignment)
            held[heavy].discard(assignment.task_id)
            held[light].add(assignment.task_id)
        total[heavy] -= len(move)
        total[light] += len(move)
        result.assignments_moved += len(move)
        result.moves[(names[heavy], names[light])] += len(move)
    db.session.commit()
    return result


# --------------------------------------------------------------------------
# Membership changes (the redistribution the thesis is about)
# --------------------------------------------------------------------------

def projects_for_group(group_id: int) -> list[Project]:
    return [
        gp.project
        for gp in GroupProject.query.filter_by(group_id=group_id).all()
        if gp.project is not None and gp.project.is_annotation_project
    ]


def release_user(user: User, project: Project) -> DistributionResult:
    """Remove a user's unfinished assignments in a project. Finished ones stay."""
    result = DistributionResult()
    open_ids = [aid for (aid,) in db.session.query(TaskAssignment.id).join(AnnotationTask).filter(
        AnnotationTask.project_id == project.id,
        TaskAssignment.user_id == user.id, TaskAssignment.is_completed.is_(False))]
    if open_ids:  # one DELETE instead of loading and deleting each assignment
        TaskAssignment.query.filter(TaskAssignment.id.in_(open_ids)).delete(synchronize_session=False)
    db.session.commit()
    result.assignments_released = len(open_ids)
    return result


def on_member_removed(user: User, group_id: int) -> DistributionResult:
    """Call after a user leaves a group (the membership row is already gone).

    For each annotation project of that group the user can no longer reach,
    their open work is released and handed to the remaining members.
    """
    result = DistributionResult()
    for project in projects_for_group(group_id):
        still_eligible = any(u.id == user.id for u in eligible_users(project))
        if still_eligible:
            continue  # still a member through another group
        result.merge(release_user(user, project))
        result.merge(assign_open_slots(project))
    return result


def on_member_added(user: User, group_id: int) -> DistributionResult:
    """Call after a user joins a group: fill open slots, then even out workloads."""
    result = DistributionResult()
    for project in projects_for_group(group_id):
        result.merge(assign_open_slots(project))
        result.merge(rebalance(project))
    return result


def on_group_detached(group_id: int, project_ids: list[int], member_ids: list[int]) -> DistributionResult:
    """Call after a group is deleted or unassigned from projects.

    Members who can no longer reach a project lose their open work there,
    which is redistributed to whoever is still eligible.
    """
    result = DistributionResult()
    for project_id in project_ids:
        project = db.session.get(Project, project_id)
        if project is None or not project.is_annotation_project:
            continue
        remaining = {u.id for u in eligible_users(project)}
        for uid in member_ids:
            if uid not in remaining:
                user = db.session.get(User, uid)
                if user:
                    result.merge(release_user(user, project))
        result.merge(assign_open_slots(project))
    return result


def release_over_cap(project: Project) -> DistributionResult:
    """After ``max_questions_per_user`` is lowered, give back unstarted work above it.

    The most recently assigned work goes back first; finished work is kept even
    if it alone exceeds the new limit.
    """
    result = DistributionResult()
    cap = project.max_questions_per_user
    if not cap:  # unset limit (old data): never take work away because of it
        return result
    users = eligible_users(project)
    loads = user_loads(project, users)
    for user in users:
        excess = loads[user.id][1] - cap
        if excess <= 0:
            continue
        surplus = (
            _project_assignments(project.id)
            .filter(TaskAssignment.user_id == user.id, TaskAssignment.is_completed.is_(False))
            .order_by(TaskAssignment.id.desc())
            .limit(excess)
            .all()
        )
        for assignment in surplus:
            db.session.delete(assignment)
        result.assignments_released += len(surplus)
    db.session.commit()
    return result


def distribute(project: Project) -> DistributionResult:
    """Full pass for a project: create missing tasks, enforce the cap, fill slots, balance loads."""
    result = generate_tasks(project)
    result.merge(release_over_cap(project))
    result.merge(assign_open_slots(project))
    result.merge(rebalance(project))
    return result


def workload_summary(project: Project) -> list[dict]:
    """Per-user totals for dashboards and the evaluation chapter."""
    users = eligible_users(project)
    loads = user_loads(project, users)
    completed = Counter(
        uid for (uid,) in db.session.query(TaskAssignment.user_id)
        .join(AnnotationTask)
        .filter(AnnotationTask.project_id == project.id, TaskAssignment.is_completed.is_(True))
    )
    return [
        {
            "user": u,
            "open": loads[u.id][0],
            "total": loads[u.id][1],
            "completed": completed.get(u.id, 0),
        }
        for u in users
    ]
