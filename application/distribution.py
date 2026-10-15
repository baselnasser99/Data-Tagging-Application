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
    3. Fairness: each open slot goes to the eligible user with the lowest load
       (ties broken by total assignments, then user id, so results are reproducible).
    4. Completeness: when someone leaves, their unfinished assignments are released
       and immediately offered to the remaining members; finished work is never touched.
    5. Integrity: regenerating tasks only adds missing (image, question) pairs;
       existing tasks, assignments and answers are kept.
"""
from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import case, func, true

from .extns import db
from .models import (
    AnnotationTask,
    GroupProject,
    Project,
    ProjectImage,
    Question,
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

    def merge(self, other: "DistributionResult") -> "DistributionResult":
        self.tasks_created += other.tasks_created
        self.assignments_created += other.assignments_created
        self.assignments_released += other.assignments_released
        self.assignments_moved += other.assignments_moved
        self.unfilled_slots += other.unfilled_slots
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
    for image in images:
        for question in questions:
            if (image.id, question.id) in existing:
                continue
            db.session.add(
                AnnotationTask(
                    project_id=project.id,
                    image_id=image.id,
                    question_id=question.id,
                    required_answers=project.answers_per_task or 1,
                    current_answers=0,
                    is_completed=False,
                )
            )
            result.tasks_created += 1
    db.session.commit()
    return result


def apply_answers_per_task(project: Project) -> None:
    """Propagate a changed ``answers_per_task`` setting to existing tasks."""
    for task in AnnotationTask.query.filter_by(project_id=project.id).all():
        task.required_answers = project.answers_per_task or 1
        task.is_completed = task.current_answers >= task.required_answers
    db.session.commit()


# --------------------------------------------------------------------------
# Assignment
# --------------------------------------------------------------------------

def assign_open_slots(project: Project, users: list[User] | None = None) -> DistributionResult:
    """Fill every task's missing answer slots with the least-loaded eligible users.

    A slot is "missing" when a task has fewer assignments than ``required_answers``.
    Tasks with the most missing slots are served first so coverage grows evenly.
    """
    result = DistributionResult()
    users = eligible_users(project) if users is None else users
    if not users:
        return result

    cap = project.max_questions_per_user or 0
    loads = user_loads(project, users)
    # heap entries: (open load, total load, user id)
    heap = [(loads[u.id][0], loads[u.id][1], u.id) for u in users if loads[u.id][1] < cap]
    heapq.heapify(heap)

    tasks = AnnotationTask.query.filter_by(project_id=project.id).all()
    holders: dict[int, set[int]] = {t.id: set() for t in tasks}
    for task_id, user_id in (
        db.session.query(TaskAssignment.task_id, TaskAssignment.user_id)
        .join(AnnotationTask)
        .filter(AnnotationTask.project_id == project.id)
        .all()
    ):
        holders[task_id].add(user_id)

    def missing(task):
        return max(0, (task.required_answers or 1) - len(holders[task.id]))

    pending = sorted((t for t in tasks if missing(t) > 0), key=lambda t: (-missing(t), t.id))

    for task in pending:
        for _ in range(missing(task)):
            skipped = []
            chosen = None
            while heap:
                entry = heapq.heappop(heap)
                if entry[2] in holders[task.id]:
                    skipped.append(entry)
                    continue
                chosen = entry
                break
            for entry in skipped:
                heapq.heappush(heap, entry)
            if chosen is None:
                result.unfilled_slots += 1
                continue
            open_load, total, uid = chosen
            db.session.add(TaskAssignment(task_id=task.id, user_id=uid))
            holders[task.id].add(uid)
            result.assignments_created += 1
            if total + 1 < cap:
                heapq.heappush(heap, (open_load + 1, total + 1, uid))

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
        "person has reached the 'Max tasks per user' limit. Raise the limit or add more people."
    )


def rebalance(project: Project, users: list[User] | None = None) -> DistributionResult:
    """Move unstarted assignments from the busiest to the least busy users.

    Runs until open loads differ by at most one, or no legal move remains
    (a move is legal when the receiver does not already hold that task and is
    under the cap). Completed assignments are never moved.
    """
    result = DistributionResult()
    users = eligible_users(project) if users is None else users
    if len(users) < 2:
        return result
    cap = project.max_questions_per_user or 0

    while True:
        loads = user_loads(project, users)
        by_open = sorted(users, key=lambda u: (loads[u.id][0], u.id))
        light, heavy = by_open[0], by_open[-1]
        if loads[heavy.id][0] - loads[light.id][0] <= 1 or loads[light.id][1] >= cap:
            break
        light_tasks = {
            tid for (tid,) in db.session.query(TaskAssignment.task_id)
            .join(AnnotationTask)
            .filter(AnnotationTask.project_id == project.id, TaskAssignment.user_id == light.id)
        }
        movable = (
            _project_assignments(project.id)
            .filter(
                TaskAssignment.user_id == heavy.id,
                TaskAssignment.is_completed.is_(False),
                ~TaskAssignment.task_id.in_(light_tasks) if light_tasks else true(),
            )
            .order_by(TaskAssignment.id.desc())
            .first()
        )
        if movable is None:
            break
        movable.user_id = light.id
        db.session.commit()
        result.assignments_moved += 1
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
    open_assignments = _project_assignments(project.id).filter(
        TaskAssignment.user_id == user.id, TaskAssignment.is_completed.is_(False)
    ).all()
    for assignment in open_assignments:
        db.session.delete(assignment)
    db.session.commit()
    result.assignments_released = len(open_assignments)
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


def distribute(project: Project) -> DistributionResult:
    """Full pass for a project: create missing tasks, fill slots, balance loads."""
    result = generate_tasks(project)
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
