'''
Define functions that can be used in html templates
'''

# Get a value of field from object
def getattr_safe(obj, name):
    return getattr(obj, name, '')

def plural(n, word, many=None):
    """plural(1, "image") -> "1 image"; plural(3, "image") -> "3 images"."""
    return f"{n} {word if n == 1 else (many or word + 's')}"


SIDEBAR_TODO_SHOWN = 2  # the rest fold away: the panel is short on a laptop screen


def sidebar_summary(user):
    """The small card at the bottom of the side panel.

    Members see their own progress and a way back to work: {"title", "lines": [(label, value)],
    "done", "total"}; done/total draw a bar when total > 0.
    Admins and managers see what is waiting on them ("Needs you"), since their dashboard already
    has the numbers: {"title", "todo": [(text, url)] shown, "more": [(text, url)] folded away}.
    """
    from .models import TaskAssignment

    if user.role == "end-user":
        mine = TaskAssignment.query.filter_by(user_id=user.id)
        total, done = mine.count(), mine.filter_by(is_completed=True).count()
        return {"title": "Your progress", "done": done, "total": total,
                "lines": [("Tasks done", f"{done} of {total}"), ("Waiting", total - done)]}

    todo = needs_attention(user)
    return {"title": "Needs you", "todo": todo[:SIDEBAR_TODO_SHOWN], "more": todo[SIDEBAR_TODO_SHOWN:],
            "lines": [], "done": 0, "total": 0}


def needs_attention(user):
    """What is waiting on an admin or manager, as [(text, url)].

    Each project's first missing step, in the same order as the "Next step" tile on its page
    (items, then questions, then handing out; for surveys questions, then a group). Admins also
    see people who are in no group, and groups nobody has joined. Managers see only their projects.
    """
    from flask import url_for

    from .models import AnnotationTask, GroupProject, Project, ProjectAdmin, User, UserGroup, UserGroupMembership

    if user.role == "admin":
        projects = Project.query.order_by(Project.id).all()
    else:
        ids = [pa.project_id for pa in ProjectAdmin.query.filter_by(user_id=user.id)]
        projects = Project.query.filter(Project.id.in_(ids)).order_by(Project.id).all() if ids else []

    todo = []
    for p in projects:
        if p.is_annotation_project:
            if not p.images:
                todo.append((f"{p.name}: add items", url_for("annotation_admin.upload_images", project_id=p.id)))
            elif not p.questions:
                todo.append((f"{p.name}: add questions", url_for("manager.edit_project_questions", project_id=p.id)))
            elif not AnnotationTask.query.filter_by(project_id=p.id).first():
                todo.append((f"{p.name}: hand out tasks",
                             url_for("annotation_admin.generate_annotation_tasks", project_id=p.id)))
        elif not p.questions:
            todo.append((f"{p.name}: add questions", url_for("manager.edit_project_questions", project_id=p.id)))
        elif not GroupProject.query.filter_by(project_id=p.id).first():
            todo.append((f"{p.name}: give it to a group", url_for("groups.assign_project_to_group")))

    if user.role == "admin":
        grouped = UserGroupMembership.query.with_entities(UserGroupMembership.user_id)
        loose = User.query.filter(User.role == "end-user", User.active.is_(True), User.id.not_in(grouped)).count()
        if loose:
            todo.append((f"{plural(loose, 'person', 'people')} in no group", url_for("admin.users", show="none")))
        for group in UserGroup.query.filter(~UserGroup.members.any()).order_by(UserGroup.name):
            todo.append((f"{group.name}: no members yet", url_for("groups.manage_group", group_id=group.id)))
    return todo
