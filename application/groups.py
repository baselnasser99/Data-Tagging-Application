"""Groups: create and delete them, manage members, and attach projects.

Adding or removing a member hands out or releases work through
application/distribution.py, so workloads stay balanced.
"""

from flask import Blueprint, request, redirect, url_for, flash, render_template, abort
from flask_login import current_user

from . import distribution, surveys
from .extns import db
from .feedback import flash_distribution, warn_idle_members
from .models import GroupProject, Project, ProjectAdmin, User, UserGroup, UserGroupMembership, UserQuestionAssignment
from .permissions import admin_required, is_project_admin, project_admin_access_required, project_admin_required
from .stats import answered, group_progress

groups = Blueprint("groups", __name__, url_prefix="/admin")


@groups.route("/groups")
@admin_required
def view_groups():
    groups = UserGroup.query.all()
    return render_template('admin/groups.html', groups=groups)


@groups.route("/create-group", methods=['GET', 'POST'])
@admin_required
def create_group():
    if request.method == 'POST':
        group_name = (request.form.get("group_name") or "").strip()
        group_desc = request.form.get("group_desc")
        if not group_name or len(group_name) > 100:
            flash('Please enter a group name of at most 100 characters.', 'danger')
            return redirect(url_for("groups.create_group"))

        # Check if group name already exists
        existing_group = UserGroup.query.filter_by(name=group_name).first()
        if existing_group:
            flash('Group name already exists!', 'danger')
            return redirect(url_for("groups.create_group"))

        group = UserGroup(name=group_name, description=group_desc)
        db.session.add(group)
        db.session.commit()

        flash("Group created successfully!", "success")
        return redirect(url_for("groups.view_groups"))
    # Handle GET request: render the group creation form
    return render_template('admin/create_group.html')

def _change_membership(group, added_message, removed_message, warn_idle=False):
    """Add or remove the member named in the posted form, then hand out or release their work."""
    action = request.form.get('action')
    if action not in ('add_user', 'remove_user'):
        return
    user_id = request.form.get('user_id')
    user = db.session.get(User, user_id)

    if action == 'add_user':
        if not user:
            flash('User not found', 'danger')
        elif user.role != 'end-user' or not user.active:  # the same people _available_users offers
            flash('Only active members can join a group, not admins, managers or removed people.', 'danger')
        elif UserGroupMembership.query.filter_by(user_id=user.id, group_id=group.id).first():
            flash('User is already in this group', 'warning')
        else:
            db.session.add(UserGroupMembership(user_id=user.id, group_id=group.id))
            db.session.commit()
            flash_distribution(assign_questions_to_new_group_member(user, group), added_message)
            if warn_idle:
                for project in distribution.projects_for_group(group.id):
                    warn_idle_members(project, [user])

    elif action == 'remove_user':
        membership = UserGroupMembership.query.filter_by(user_id=user_id, group_id=group.id).first()
        if membership and user:
            db.session.delete(membership)
            db.session.commit()
            flash_distribution(remove_pending_assignments_from_removed_group_member(user, group), removed_message)
        else:
            flash('User not found in group or user object not found', 'danger')


def _available_users(group):
    """Active members who are not in the group yet (removed people can't be given work)."""
    current_member_ids = [m.user_id for m in group.members]
    return User.query.filter(
        User.role == 'end-user', User.active.is_(True), ~User.id.in_(current_member_ids)
    ).all()


@groups.route("/manage-group/<int:group_id>", methods=['GET', 'POST'])
@admin_required
def manage_group(group_id):
    group = db.get_or_404(UserGroup, group_id)

    if request.method == 'POST':
        _change_membership(group, 'User added to group successfully!', 'User removed from group successfully!',
                           warn_idle=True)
        return redirect(url_for("groups.manage_group", group_id=group_id))

    return render_template("admin/manage_group.html", group=group, available_users=_available_users(group))


@groups.route("/project/<int:project_id>/manage-group/<int:group_id>", methods=['GET', 'POST'])
@project_admin_required
def manage_group_for_project(project_id, group_id):
    group = db.get_or_404(UserGroup, group_id)

    # Verify that the group is assigned to the project the admin has access to
    group_project_link = GroupProject.query.filter_by(group_id=group_id, project_id=project_id).first()
    if not group_project_link:
        flash('This group is not associated with your project, or you do not have permission to manage it.', 'danger')
        return redirect(url_for('manager.project_groups', project_id=project_id))

    other_projects = _other_managers_projects(group)

    if request.method == 'POST':
        if other_projects:
            abort(403)
        _change_membership(group, 'User added to group successfully and assignments updated',
                           'User removed from group; their unfinished tasks were handed to the rest of the team')
        return redirect(url_for("groups.manage_group_for_project", project_id=project_id, group_id=group_id))

    # On a group they can't manage, a manager sees how many people it has, not who (the page hides them).
    return render_template("admin/manage_group.html", group=group, project_id=project_id, other_projects=other_projects,
                           available_users=[] if other_projects else _available_users(group))


def _other_managers_projects(group):
    """The group's projects the signed-in person doesn't run (none for admins).

    Membership is group-wide, so it changes the work of every project the group is on:
    a manager may change it, or give the group more work, only when all of those are theirs.
    """
    return [gp.project for gp in group.assigned_projects
            if current_user.role != 'admin' and not is_project_admin(gp.project_id)]


def can_use_group(group):
    """May the signed-in person attach one of their projects to this group (and see who is in it)?"""
    return not _other_managers_projects(group)


def usable_groups(project=None):
    """The groups the signed-in person may choose on a form, plus any already on ``project``."""
    on_project = {gp.group_id for gp in project.assigned_groups} if project else set()
    return [g for g in UserGroup.query.all() if g.id in on_project or can_use_group(g)]


def set_up_new_project(project, group_id):
    """A manager who creates a project manages it; the group chosen on the form (if any) gets it.

    Tasks are not handed out here: that happens once items are uploaded.
    """
    if current_user.role == 'project-admin':
        db.session.add(ProjectAdmin(user_id=current_user.id, project_id=project.id, assigned_by=current_user.id))
    group = db.session.get(UserGroup, group_id) if group_id else None
    if group and not can_use_group(group):
        flash(f'"{project.name}" was not given to group "{group.name}": that group also works on other '
              'managers\' projects. Choose another group, or ask an admin.', 'warning')
        group = None
    if group and not GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first():
        db.session.add(GroupProject(group_id=group.id, project_id=project.id))


def assign_questions_to_new_group_member(user, group):
    """Give a new group member their share of the group's projects.

    Survey projects: every member answers every question (application/surveys.py).
    Annotation projects: open slots are filled and workloads rebalanced
    (see application/distribution.py).
    """
    surveys.give_to_new_member(user, group)
    return distribution.on_member_added(user, group.id)


def remove_pending_assignments_from_removed_group_member(user, group):
    """Clean up after a member has left a group (call after deleting the membership).

    Survey projects: unanswered question assignments are removed.
    Annotation projects: unfinished tasks are released and redistributed to the
    remaining members. Completed work is always kept.
    """
    for gp in GroupProject.query.filter_by(group_id=group.id).all():
        project = gp.project
        if project is None or project.is_annotation_project:
            continue
        assignments = UserQuestionAssignment.query.filter_by(
            user_id=user.id, group_project_id=f"{group.id}-{project.id}").all()
        done = answered(assignments)
        for assignment in assignments:
            if (assignment.user_id, assignment.question_id) not in done:
                db.session.delete(assignment)
    db.session.commit()
    return distribution.on_member_removed(user, group.id)


@groups.route("/assign-project-to-group", methods=['GET', 'POST'])
@project_admin_access_required
def assign_project_to_group():
    if request.method == 'POST':
        group_id = request.form.get('group_id')
        project_id = request.form.get('project_id')
        group = db.session.get(UserGroup, group_id)
        project = db.session.get(Project, project_id)

        if project and current_user.role != 'admin' and not is_project_admin(project.id):
            abort(403)  # managers may only attach their own projects
        if group and project:
            # Check if project is already assigned to group
            existing = GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first()
            if existing:
                flash('This project is already assigned to this group', 'warning')
                return redirect(url_for("groups.assign_project_to_group"))
            if not can_use_group(group):
                abort(403)  # the group also works on other managers' projects

            # Create group project assignment
            group_project = GroupProject(group_id=group.id, project_id=project.id)
            db.session.add(group_project)
            db.session.commit()
            # Logic for distributing questions/tasks based on project type and content
            if project.is_annotation_project:
                if project.images and project.questions:
                    result = distribution.distribute(project)
                    flash_distribution(result, f'Project "{project.name}" assigned to group "{group.name}".')
                    if not group.members:
                        flash(f'Group "{group.name}" has no members yet. They will get tasks when people join.', 'warning')
                    warn_idle_members(project, [m.user for m in group.members])
                else:
                    flash(f'Project "{project.name}" assigned to group "{group.name}". Upload images and add questions to create tasks.', 'warning')
            else: # Regular survey project
                questions = project.questions
                if questions:
                    surveys.give_to_group(group, project)
                    flash(f'Project "{project.name}" assigned to group "{group.name}" and questions distributed successfully!', 'success')
                else:
                    flash(f'Project "{project.name}" assigned to group "{group.name}". No questions found yet. Please add questions to the project.', 'warning')
            
            return redirect(url_for("groups.assign_project_to_group"))
        else:
            flash('Invalid selection', 'danger')

    if current_user.role == 'admin':
        # System admins can assign any project
        projects = Project.query.all()
    else:
        # Project admins can only assign projects they manage
        managed_project_ids = [pa.project_id for pa in ProjectAdmin.query.filter_by(user_id=current_user.id).all()]
        projects = Project.query.filter(Project.id.in_(managed_project_ids)).all()
    # only the projects this person may see; a manager must not see others' progress
    shown_ids = {project.id for project in projects}
    # A manager sees the groups they may choose, and those already on their projects; of a group
    # that also works for other managers, only its size (locked: not offered, members not named).
    everyone = UserGroup.query.all()
    locked = {g.id for g in everyone if not can_use_group(g)}
    groups = [g for g in everyone
              if g.id not in locked or any(gp.project_id in shown_ids for gp in g.assigned_projects)]
    progress = {(gp.group_id, gp.project_id): group_progress(group, [gp.project_id])
                for group in groups for gp in group.assigned_projects if gp.project_id in shown_ids}
    return render_template("admin/assign_project_to_group.html", groups=groups, projects=projects, progress=progress,
                           locked=locked)


@groups.route("/delete-group/<int:group_id>", methods=["POST"])
@admin_required
def delete_group(group_id):
    group = db.get_or_404(UserGroup, group_id)
    project_ids = [gp.project_id for gp in group.assigned_projects]
    member_ids = [m.user_id for m in group.members]

    # Survey question assignments that belonged to this group
    for project_id in project_ids:
        UserQuestionAssignment.query.filter_by(group_project_id=f"{group.id}-{project_id}").delete()

    # Memberships and project links are removed by the model's cascade rules
    db.session.delete(group)
    db.session.commit()

    # Members who no longer reach a project hand their unfinished tasks back
    result = distribution.on_group_detached(group_id, project_ids, member_ids)
    flash_distribution(result)
    flash("Group deleted successfully!", "info")
    return redirect(url_for("groups.view_groups"))
