"""Flash messages shared by the admin, group and manager pages."""
from flask import flash

from . import distribution
from .jinja_utils import plural


def flash_distribution(result, success_message=None):
    """Show what a distribution run did, plus any warnings."""
    if success_message:
        flash(success_message, "success")
    details = []
    if result.tasks_created:
        details.append(f"{plural(result.tasks_created, 'new task')} created")
    if result.assignments_created:
        details.append(f"{plural(result.assignments_created, 'assignment')} given out")
    if result.assignments_released:
        details.append(f"{plural(result.assignments_released, 'unfinished assignment')} released")
    if result.assignments_moved:
        who = ", ".join(f"{a} → {b}: {n}" for (a, b), n in result.moves.items())
        details.append(f"{plural(result.assignments_moved, 'unstarted assignment')} moved to balance workloads ({who})")
    if details:
        flash("Task distribution: " + ", ".join(details) + ".", "info")
    for warning in result.warnings:
        flash(warning, "warning")


def warn_idle_members(project, users):
    """Say so when people working on a project got no tasks because every task is already taken."""
    if not users or not project.is_annotation_project or not project.annotation_tasks:
        return
    loads = distribution.user_loads(project, users)
    idle = [u for u in users if loads[u.id][1] == 0]
    if idle:
        names = ", ".join(u.full_name for u in idle) if len(idle) <= 3 else plural(len(idle), "person", "people")
        flash(f"{names} got no tasks in '{project.name}': all tasks are already assigned. "
              f"Raise \"Answers per task\" to give them work.", "warning")
