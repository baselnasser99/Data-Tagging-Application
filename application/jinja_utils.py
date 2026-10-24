'''
Define functions that can be used in html templates
'''

# Get a value of field from object
def getattr_safe(obj, name):
    return getattr(obj, name, '')

def plural(n, word, many=None):
    """plural(1, "image") -> "1 image"; plural(3, "image") -> "3 images"."""
    return f"{n} {word if n == 1 else (many or word + 's')}"


SIDEBAR_TODO_SHOWN = 2   # the panel is short on a laptop screen
SIDEBAR_TODO_FOLDED = 8  # under "and N more"; anything beyond is counted, with a link to the projects page


def sidebar_summary(user):
    """The small card at the bottom of the side panel.

    Members see their own progress and a way back to work: {"title", "lines": [(label, value)],
    "done", "total"}; done/total draw a bar when total > 0.
    Admins and managers see what is waiting on them ("Needs you"), since their dashboard already
    has the numbers: {"title", "todo": [(text, url)] shown, "more": [(text, url)] folded away,
    "rest": how many beyond those, "rest_url": where to see them}.
    """
    from .models import TaskAssignment

    if user.role == "end-user":
        mine = TaskAssignment.query.filter_by(user_id=user.id)
        total, done = mine.count(), mine.filter_by(is_completed=True).count()
        return {"title": "Your progress", "done": done, "total": total,
                "lines": [("Tasks done", f"{done} of {total}"), ("Waiting", total - done)]}

    from flask import url_for

    todo = needs_attention(user)
    shown, folded = SIDEBAR_TODO_SHOWN, SIDEBAR_TODO_SHOWN + SIDEBAR_TODO_FOLDED
    return {"title": "Needs you", "todo": todo[:shown], "more": todo[shown:folded], "rest": max(len(todo) - folded, 0),
            "rest_url": url_for("admin.view_projects" if user.role == "admin" else "manager.my_projects"),
            "lines": [], "done": 0, "total": 0}


def needs_attention(user):
    """What is waiting on an admin or manager, as [(text, url)].

    Admins first see people who are in no group and groups nobody has joined (at most two lines,
    so many projects never push them out of sight). Then each project's first missing step, in the
    same order as the "Next step" tile on its page (items, then questions, then handing out; for
    surveys questions, then a group). And items members marked "can't tell" in the last 7 days:
    there is no "dealt with" state, so older ones drop off instead of staying on the card for good.
    Managers see only their own projects.

    It runs on every page, so the number of queries stays the same however many projects there
    are: one pass over each table says which projects have items, questions, tasks and a group.
    """
    from flask import url_for

    from .extns import db
    from datetime import datetime, timedelta

    from sqlalchemy import distinct, func

    from .models import (AnnotationTask, GroupProject, Project, ProjectAdmin, ProjectImage, Question, QuestionAnswer,
                         TaskAssignment, User, UserGroup, UserGroupMembership)

    projects = db.session.query(Project.id, Project.name, Project.is_annotation_project).order_by(Project.id)
    if user.role != "admin":
        projects = projects.filter(Project.id.in_(db.session.query(ProjectAdmin.project_id)
                                                  .filter(ProjectAdmin.user_id == user.id)))

    def having(column):
        return {pid for (pid,) in db.session.query(column).distinct()}

    with_items, with_questions = having(ProjectImage.project_id), having(Question.project_id)
    with_tasks, with_group = having(AnnotationTask.project_id), having(GroupProject.project_id)
    unclear = dict(db.session.query(AnnotationTask.project_id, func.count(distinct(AnnotationTask.image_id)))
                   .join(TaskAssignment, TaskAssignment.task_id == AnnotationTask.id)
                   .join(QuestionAnswer, QuestionAnswer.task_assignment_id == TaskAssignment.id)
                   .filter(QuestionAnswer.cant_tell.is_(True),
                           QuestionAnswer.submitted_date >= datetime.now() - timedelta(days=7))
                   .group_by(AnnotationTask.project_id))

    todo = []
    if user.role == "admin":
        grouped = UserGroupMembership.query.with_entities(UserGroupMembership.user_id)
        loose = User.query.filter(User.role == "end-user", User.active.is_(True), User.id.not_in(grouped)).count()
        if loose:
            todo.append((f"{plural(loose, 'person', 'people')} in no group", url_for("admin.users", show="none")))
        empty = UserGroup.query.filter(~UserGroup.members.any()).order_by(UserGroup.name).all()
        if len(empty) == 1:
            todo.append((f"{empty[0].name}: no members yet", url_for("groups.manage_group", group_id=empty[0].id)))
        elif empty:
            todo.append((f"{len(empty)} groups with no members", url_for("groups.view_groups")))

    for pid, name, is_annotation in projects:
        if is_annotation:
            if pid not in with_items:
                todo.append((f"{name}: add items", url_for("annotation_admin.upload_images", project_id=pid)))
            elif pid not in with_questions:
                todo.append((f"{name}: add questions", url_for("manager.edit_project_questions", project_id=pid)))
            elif pid not in with_tasks:
                todo.append((f"{name}: hand out tasks", url_for("annotation_admin.generate_annotation_tasks", project_id=pid)))
        elif pid not in with_questions:
            todo.append((f"{name}: add questions", url_for("manager.edit_project_questions", project_id=pid)))
        elif pid not in with_group:
            todo.append((f"{name}: give it to a group", url_for("groups.assign_project_to_group")))
        if unclear.get(pid):
            todo.append((f"{name}: {plural(unclear[pid], 'item')} marked can't tell",
                         url_for("annotation_admin.view_annotation_project", project_id=pid)))
    return todo
