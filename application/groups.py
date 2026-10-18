"""Groups: create and delete them, manage members, and attach projects.

Adding or removing a member hands out or releases work through
application/distribution.py, so workloads stay balanced.
"""

from flask import Blueprint, request, redirect, url_for, flash, render_template, session, abort

from . import distribution
from .extns import db
from .feedback import flash_distribution, warn_idle_members
from .models import AnnotationTask, Project, ProjectAdmin, Question, TaskAssignment, User, UserGroup, UserGroupMembership, GroupProject, UserQuestionAssignment, QuestionAnswer
from .permissions import admin_required, is_project_admin, project_admin_access_required, project_admin_required

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
        group_name = request.form.get("group_name")
        group_desc = request.form.get("group_desc")

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

@groups.route("/manage-group/<int:group_id>", methods=['GET', 'POST'])
@admin_required
def manage_group(group_id):
    group = db.get_or_404(UserGroup, group_id)

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_user':
            user_id = request.form.get('user_id')
            user = db.session.get(User, user_id)

            if user:
                existing = UserGroupMembership.query.filter_by(user_id=user.id, group_id=group.id).first()
                if existing:
                    flash('User is already in this group', 'warning')
                else:
                    membership = UserGroupMembership(user_id=user.id, group_id=group.id)
                    db.session.add(membership)
                    db.session.commit()
                    result = assign_questions_to_new_group_member(user, group)
                    flash_distribution(result, 'User added to group successfully!')
                    for project in distribution.projects_for_group(group.id):
                        warn_idle_members(project, [user])
            else:
                flash('User not found', 'danger')

        elif action == 'remove_user':
            user_id = request.form.get('user_id')
            user = db.session.get(User, user_id)
            membership = UserGroupMembership.query.filter_by(user_id=user_id, group_id=group.id).first()
            if membership and user:
                db.session.delete(membership)
                db.session.commit()
                result = remove_pending_assignments_from_removed_group_member(user, group)
                flash_distribution(result, 'User removed from group successfully!')
            else:
                flash('User not found in group or user object not found', 'danger')

        return redirect(url_for("groups.manage_group", group_id=group_id))

    current_member_ids = [m.user_id for m in group.members]
    available_users = User.query.filter(
        User.role == 'end-user',
        ~User.id.in_(current_member_ids)
    ).all()

    return render_template("admin/manage_group.html", group=group, available_users=available_users)

@groups.route("/project/<int:project_id>/manage-group/<int:group_id>", methods=['GET', 'POST'])
@project_admin_required
def manage_group_for_project(project_id, group_id):
    group = db.get_or_404(UserGroup, group_id)

    # Verify that the group is assigned to the project the admin has access to
    group_project_link = GroupProject.query.filter_by(group_id=group_id, project_id=project_id).first()
    if not group_project_link:
        flash('This group is not associated with your project, or you do not have permission to manage it.', 'danger')
        return redirect(url_for('manager.project_groups', project_id=project_id))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_user':
            user_id = request.form.get('user_id')
            user = db.session.get(User, user_id)

            if user:
                existing = UserGroupMembership.query.filter_by(user_id=user.id, group_id=group.id).first()
                if existing:
                    flash('User is already in this group', 'warning')
                else:
                    membership = UserGroupMembership(user_id=user.id, group_id=group.id)
                    db.session.add(membership)
                    db.session.commit()
                    result = assign_questions_to_new_group_member(user, group)
                    flash_distribution(result, 'User added to group successfully and assignments updated')
            else:
                flash('User not found', 'danger')

        elif action == 'remove_user':
            user_id = request.form.get('user_id')
            user = db.session.get(User, user_id)
            membership = UserGroupMembership.query.filter_by(user_id=user_id, group_id=group.id).first()
            if membership and user:
                db.session.delete(membership)
                db.session.commit()
                result = remove_pending_assignments_from_removed_group_member(user, group)
                flash_distribution(result, 'User removed from group; their unfinished tasks were handed to the rest of the team')
            else:
                flash('User not found in group or user object not found', 'danger')

        return redirect(url_for("groups.manage_group_for_project", project_id=project_id, group_id=group_id))

    current_member_ids = [m.user_id for m in group.members]
    available_users = User.query.filter(
        User.role == 'end-user',
        ~User.id.in_(current_member_ids)
    ).all()

    return render_template("admin/manage_group.html", group=group, available_users=available_users)


def group_progress(group, project_ids):
    """(done, total) of the work the group's members hold in these projects.

    Annotation work counts finished task assignments; survey work counts
    answered question assignments.
    """
    member_ids = [m.user_id for m in group.members]
    tasks = TaskAssignment.query.join(AnnotationTask).filter(
        TaskAssignment.user_id.in_(member_ids), AnnotationTask.project_id.in_(project_ids))
    survey = UserQuestionAssignment.query.filter(
        UserQuestionAssignment.user_id.in_(member_ids),
        UserQuestionAssignment.group_project_id.in_([f"{group.id}-{pid}" for pid in project_ids]))
    total = tasks.count() + survey.count()
    done = tasks.filter(TaskAssignment.is_completed.is_(True)).count() + sum(
        1 for q in survey if QuestionAnswer.query.filter_by(
            user_id=q.user_id, question_id=q.question_id, task_assignment_id=None).first())
    return done, total


def set_up_new_project(project, group_id):
    """A manager who creates a project manages it; the group chosen on the form (if any) gets it.

    Tasks are not handed out here: that happens once items are uploaded.
    """
    if session.get('role') == 'project-admin':
        db.session.add(ProjectAdmin(user_id=session['id'], project_id=project.id, assigned_by=session['id']))
    group = db.session.get(UserGroup, group_id) if group_id else None
    if group and not GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first():
        db.session.add(GroupProject(group_id=group.id, project_id=project.id))


def project_progress(project):
    """(done, total) for a whole project: finished tasks, or answered survey questions."""
    if project.is_annotation_project:
        tasks = AnnotationTask.query.filter_by(project_id=project.id)
        return tasks.filter_by(is_completed=True).count(), tasks.count()
    assigned = UserQuestionAssignment.query.join(Question).filter(Question.project_id == project.id).all()
    done = sum(1 for q in assigned if QuestionAnswer.query.filter_by(
        user_id=q.user_id, question_id=q.question_id, task_assignment_id=None).first())
    return done, len(assigned)


def assign_questions_to_new_group_member(user, group):
    """Give a new group member their share of the group's projects.

    Survey projects: every member answers every question.
    Annotation projects: open slots are filled and workloads rebalanced
    (see application/distribution.py).
    """
    for gp in GroupProject.query.filter_by(group_id=group.id).all():
        project = gp.project
        if project is None or project.is_annotation_project:
            continue
        group_project_id_str = f"{group.id}-{project.id}"
        for question in project.questions:
            if not UserQuestionAssignment.query.filter_by(
                user_id=user.id, question_id=question.id, group_project_id=group_project_id_str
            ).first():
                db.session.add(UserQuestionAssignment(
                    user_id=user.id, question_id=question.id, group_project_id=group_project_id_str
                ))
    db.session.commit()
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
        for assignment in UserQuestionAssignment.query.filter_by(
            user_id=user.id, group_project_id=f"{group.id}-{project.id}"
        ).all():
            if not QuestionAnswer.query.filter_by(user_id=user.id, question_id=assignment.question_id).first():
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

        if project and session.get('role') != 'admin' and not is_project_admin(project.id):
            abort(403)  # managers may only attach their own projects
        if group and project:
            # Check if project is already assigned to group
            existing = GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first()
            if existing:
                flash('This project is already assigned to this group', 'warning')
                return redirect(url_for("groups.assign_project_to_group"))

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
                    distribute_questions_to_group(group, project)
                    flash(f'Project "{project.name}" assigned to group "{group.name}" and questions distributed successfully!', 'success')
                else:
                    flash(f'Project "{project.name}" assigned to group "{group.name}". No questions found yet. Please add questions to the project.', 'warning')
            
            return redirect(url_for("groups.assign_project_to_group"))
        else:
            flash('Invalid selection', 'danger')

    groups = UserGroup.query.all()
    
    current_user_id = session.get('id')
    current_user_role = session.get('role')
    if current_user_role == 'admin':
        # System admins can assign any project
        projects = Project.query.all()
    else:
        # Project admins can only assign projects they manage
        managed_project_ids = [pa.project_id for pa in ProjectAdmin.query.filter_by(user_id=current_user_id).all()]
        projects = Project.query.filter(Project.id.in_(managed_project_ids)).all()
    # only the projects this person may see; a manager must not see others' progress
    shown_ids = {project.id for project in projects}
    progress = {(gp.group_id, gp.project_id): group_progress(group, [gp.project_id])
                for group in groups for gp in group.assigned_projects if gp.project_id in shown_ids}
    return render_template("admin/assign_project_to_group.html", groups=groups, projects=projects, progress=progress)


def distribute_questions_to_group(group, project):
    """Distribute questions evenly among group members for survey projects, or based on annotation tasks for annotation projects"""
    members = [m.user for m in group.members if m.user]
    questions = project.questions

    if not members or not questions:
        return

    # Create a unique identifier for this group-project combination
    group_project_id = f"{group.id}-{project.id}"

    # Clear existing assignments for this group-project combination before re-distributing
    UserQuestionAssignment.query.filter_by(group_project_id=group_project_id).delete()
    db.session.commit()

    if project.is_annotation_project:
        distribution.distribute(project)

    else: # This is a regular survey project
        # For survey projects, each user in the group should receive ALL questions from the project
        for member in members:
            for question in questions:
                existing_assignment = UserQuestionAssignment.query.filter_by(
                    user_id=member.id,
                    question_id=question.id,
                    group_project_id=group_project_id
                ).first()
                
                if not existing_assignment:
                    db.session.add(UserQuestionAssignment(
                        user_id=member.id,
                        question_id=question.id,
                        group_project_id=group_project_id,
                    ))
    # Mark questions as distributed (this flag might need review based on exact workflow, but keeping for now)
    group_project_link = GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first()
    if group_project_link:
        group_project_link.questions_distributed = True

    db.session.commit()


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
