"""Project managers ("project-admin" role): their projects, dashboards, managers,
groups, results and question editing. System admins can open these pages too."""
import logging
from datetime import datetime, timedelta

from flask import Blueprint, request, redirect, url_for, flash, render_template
from flask_login import current_user

from . import distribution
from .auth import MIN_PASSWORD_LENGTH
from .extns import db
from .feedback import flash_distribution
from .groups import distribute_questions_to_group
from .models import (AnnotationTask, AnswerType, Project, ProjectAdmin, ProjectImage, Question,
                     QuestionAnswer, TaskAssignment, User, UserQuestionAssignment)
from .permissions import admin_required, project_admin_access_required, project_admin_required
from .stats import group_stats_for, project_progress, weekly_answers, workload_balance
from .uploads import save_attachment

audit = logging.getLogger("audit")

manager = Blueprint("manager", __name__, url_prefix="/admin")


@manager.route("/my-projects")
@project_admin_access_required
def my_projects():
    """A manager's home: the admin dashboard's layout, for the projects they manage."""
    projects = [pa.project for pa in ProjectAdmin.query.filter_by(user_id=current_user.id) if pa.project]
    progress = {p.id: project_progress(p) for p in projects}
    project_ids = [p.id for p in projects]
    people = {m.user_id for p in projects for gp in p.assigned_groups if gp.group for m in gp.group.members}
    week_ago = datetime.now() - timedelta(days=7)
    recent = QuestionAnswer.query.join(Question).filter(
        Question.project_id.in_(project_ids), QuestionAnswer.submitted_date >= week_ago).count() if project_ids else 0
    groups = {gp.group.id: gp.group for p in projects for gp in p.assigned_groups if gp.group}
    weeks_data = weekly_answers(project_ids)
    # Answers, as on the admin dashboard: a project's tasks need answers_per_task answers each,
    # a survey's questions one answer per member (project_progress already counts those).
    held = TaskAssignment.query.join(AnnotationTask).filter(AnnotationTask.project_id.in_(project_ids))
    surveys = [p for p in projects if not p.is_annotation_project]
    figures = {
        "projects": len(projects),
        "done": held.filter(TaskAssignment.is_completed.is_(True)).count() + sum(progress[p.id][0] for p in surveys),
        "total": held.count() + sum(progress[p.id][1] for p in surveys),
        "people": len(people),
        "groups": len(groups),
        "recent": recent,
        "week_before": weeks_data[-2]['answers'] if len(weeks_data) > 1 else 0,
    }
    balance = [b for b in (workload_balance(p) for p in projects if p.is_annotation_project) if b['open']]
    return render_template("admin/my_projects.html", projects=projects, progress=progress, figures=figures,
                           weeks_data=weeks_data, balance=balance, today=datetime.now(),
                           group_stats=group_stats_for(groups.values(), set(project_ids)))

@manager.route("/project/<int:project_id>/dashboard")
@project_admin_required
def project_dashboard(project_id):
    project = db.get_or_404(Project, project_id)
    
    # Get project statistics
    questions_count = Question.query.filter_by(project_id=project_id).count()
    
    # For annotation projects, get additional statistics
    annotation_stats = None
    if project.is_annotation_project:
        images_count = ProjectImage.query.filter_by(project_id=project_id).count()
        tasks_count = AnnotationTask.query.filter_by(project_id=project_id).count()
        completed_tasks = AnnotationTask.query.filter_by(project_id=project_id, is_completed=True).count()
        
        # Get assignment statistics
        assignments = TaskAssignment.query.join(AnnotationTask).filter(AnnotationTask.project_id == project_id).all()
        total_assignments = len(assignments)
        completed_assignments = len([a for a in assignments if a.is_completed])
        
        # Calculate completion percentages
        completion_percentage = (completed_tasks / tasks_count * 100) if tasks_count > 0 else 0
        assignment_completion = (completed_assignments / total_assignments * 100) if total_assignments > 0 else 0
        
        annotation_stats = {
            'images_count': images_count,
            'tasks_count': tasks_count,
            'completed_tasks': completed_tasks,
            'completion_percentage': round(completion_percentage, 1),
            'total_assignments': total_assignments,
            'completed_assignments': completed_assignments,
            'assignment_completion': round(assignment_completion, 1)
        }
    
    # Get assigned groups
    assigned_groups = [gp.group for gp in project.assigned_groups]
    people = {m.user_id for g in assigned_groups if g for m in g.members}

    # Get project admins
    project_admins = ProjectAdmin.query.filter_by(project_id=project_id).all()

    balance = workload_balance(project) if project.is_annotation_project else None
    return render_template(
        "admin/project_dashboard.html",
        project=project,
        questions_count=questions_count,
        annotation_stats=annotation_stats,
        assigned_groups=assigned_groups,
        people_count=len(people),
        project_admins=project_admins,
        balance=[balance] if balance and balance['people'] and balance['share_max'] else [],
        survey_progress=None if project.is_annotation_project else project_progress(project),
    )

@manager.route("/project/<int:project_id>/admins")
@project_admin_required
def project_admins(project_id):
    project = db.get_or_404(Project, project_id)
    project_admins = ProjectAdmin.query.filter_by(project_id=project_id).all()
    return render_template("admin/project_admins.html", project=project, project_admins=project_admins,
                           candidates=manager_candidates())

def _chosen_user(form):
    """The person picked in an 'add a manager' form: a typed email wins over the list."""
    email = (form.get('email') or '').strip()
    if email:
        return User.query.filter_by(email=email).first()
    return db.session.get(User, form.get('user_id', type=int) or 0)


def add_manager(project, user):
    """Make `user` the manager of `project`. Returns (message, flash category)."""
    if user is None:
        return 'No user with that email was found. Pick someone from the list, or create a new manager.', 'danger'
    if ProjectAdmin.query.filter_by(project_id=project.id).first():
        return f'"{project.name}" already has a manager. Remove them first; a project has one manager.', 'danger'
    db.session.add(ProjectAdmin(user_id=user.id, project_id=project.id, assigned_by=current_user.id))
    if user.role not in ('project-admin', 'admin'):
        user.role = 'project-admin'
    db.session.commit()
    return f'{user.full_name} now manages "{project.name}".', 'success'


def manager_candidates():
    """Everyone who could be made a manager (system admins already see everything)."""
    return User.query.filter(User.role != 'admin').order_by(User.full_name).all()


@manager.route("/project/<int:project_id>/admins/add", methods=['POST'])
@project_admin_required
def add_project_admin(project_id):
    project = db.get_or_404(Project, project_id)
    flash(*add_manager(project, _chosen_user(request.form)))
    return redirect(url_for('manager.project_admins', project_id=project_id))


@manager.route("/managers")
@admin_required
def managers():
    """Every project with its manager, and a form to add one, in one place."""
    projects = Project.query.order_by(Project.name).all()
    by_project = {pa.project_id: pa for pa in ProjectAdmin.query.all()}
    managers = [(u, [pa.project for pa in by_project.values() if pa.user_id == u.id])
                for u in User.query.filter_by(role='project-admin').order_by(User.full_name)]
    return render_template("admin/managers.html", projects=projects, by_project=by_project,
                           candidates=manager_candidates(), managers=managers)


@manager.route("/managers/add", methods=['POST'])
@admin_required
def add_manager_anywhere():
    project = db.get_or_404(Project, request.form.get('project_id', type=int) or 0)
    flash(*add_manager(project, _chosen_user(request.form)))
    return redirect(url_for('manager.managers'))

@manager.route("/project/<int:project_id>/admins/<int:admin_id>/remove", methods=['POST'])
@project_admin_required
def remove_project_admin(project_id, admin_id):
    project_admin = db.get_or_404(ProjectAdmin, admin_id)

    # Ensure the project admin being removed is for the current project
    if project_admin.project_id != project_id:
        flash('Invalid project admin.', 'danger')
        return redirect(url_for('manager.project_admins', project_id=project_id))
    
    # Check if current user has permission to remove this project admin
    # System admins can remove any project admin
    # Project admins can only remove project admins they assigned or other project admins (but not themselves)
    if current_user.role != 'admin':
        # Project admins can't remove themselves
        if project_admin.user_id == current_user.id:
            flash('You cannot remove yourself as a project admin.', 'danger')
            return redirect(url_for('manager.project_admins', project_id=project_id))

        # Project admins can only remove project admins they assigned or other project admins
        # (System admins can remove any project admin)
        assigner = project_admin.assigner  # None if that account was deleted since
        if project_admin.assigned_by != current_user.id and assigner and assigner.role == 'admin':
            flash('You cannot remove a project admin assigned by a system admin.', 'danger')
            return redirect(url_for('manager.project_admins', project_id=project_id))

    user = project_admin.user  # whose project admin role is being removed
    db.session.delete(project_admin)
    db.session.commit()

    # With no other project roles, a manager goes back to being a member
    if user.role == 'project-admin' and not ProjectAdmin.query.filter_by(user_id=user.id).first():
        user.role = 'end-user'
        db.session.commit()

    flash('Project admin removed successfully.', 'success')
    return redirect(url_for('manager.project_admins', project_id=project_id))

@manager.route("/project/<int:project_id>/admins/create", methods=['GET', 'POST'])
@project_admin_required
def create_project_admin(project_id):
    """Create a new user and assign them as a project admin"""
    project = db.get_or_404(Project, project_id)

    if request.method == 'POST':
        fullname = (request.form.get('fullname') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''

        # The same checks as admin.create_user
        if not fullname or not email:
            flash('Please enter a name and an email address.', 'danger')
            return redirect(url_for('manager.create_project_admin', project_id=project_id))
        if User.query.filter_by(email=email).first():
            flash('A user with this email already exists.', 'danger')
            return redirect(url_for('manager.create_project_admin', project_id=project_id))
        if len(password) < MIN_PASSWORD_LENGTH:
            flash(f'The password must be at least {MIN_PASSWORD_LENGTH} characters long.', 'danger')
            return redirect(url_for('manager.create_project_admin', project_id=project_id))

        # Check if a project admin already exists for this project
        existing_project_manager = ProjectAdmin.query.filter_by(project_id=project_id).first()
        if existing_project_manager:
            flash('This project already has a manager. Only one manager is allowed per project.', 'danger')
            return redirect(url_for('manager.project_admins', project_id=project_id))

        # Create new user and assign as project admin
        new_user = User()
        new_user.register_project_admin(fullname, email, password, project_id, current_user.id)
        audit.info("USER_CREATED user_id=%s role=%s by=%s", new_user.id, new_user.role, current_user.id)

        flash('New project admin created successfully.', 'success')
        return redirect(url_for('manager.project_admins', project_id=project_id))

    return render_template('admin/create_project_admin.html', project=project,
                           min_password_length=MIN_PASSWORD_LENGTH)

@manager.route("/project/<int:project_id>/groups")
@project_admin_required
def project_groups(project_id):
    project = db.get_or_404(Project, project_id)
    # In a real implementation, you'd fetch groups assigned to this project
    assigned_groups = [gp.group for gp in project.assigned_groups]
    return render_template("admin/project_groups.html", project=project, assigned_groups=assigned_groups)

@manager.route("/project/<int:project_id>/edit-questions", methods=['GET', 'POST'])
@project_admin_required
def edit_project_questions(project_id):
    """Edit questions and settings. The page has one form per action."""
    project = db.get_or_404(Project, project_id)
    if request.method == 'POST':
        action = _EDIT_ACTIONS.get(request.form.get('action'))
        if action:
            return action(project)
    return render_template("admin/edit_project_questions.html", project=project)


def _update_questions(project):
    """Save the wording, answer type, options and file of every question on the form.

    Settings have their own form ("Update Settings"); this form never sends them.
    """
    form = request.form.to_dict(flat=True)
    files = request.files.to_dict(flat=True)
    for key, value in form.items():
        if key.startswith('question-'):
            question = db.session.get(Question, int(key.split('-')[1]))
            if question:
                _update_question(question, value, form, files)

    db.session.commit()
    flash('Project questions and settings updated successfully!', 'success')
    if project.is_annotation_project:
        flash_distribution(distribution.apply_answers_per_task(project).merge(distribution.distribute(project)))
    return redirect(url_for('manager.project_dashboard', project_id=project.id))


def _update_question(question, text, form, files):
    qid = question.id
    has_answers = QuestionAnswer.query.filter_by(question_id=qid).count() > 0
    if text.strip() != question.question.strip() and has_answers:
        flash(f'"{question.question}" was reworded to "{text}". Its existing answers were given to the old '
              'wording and are kept as they are. If the meaning changed, add a new question instead.', 'warning')
    question.question = text

    if f'dropdown-{qid}' in form:
        new_type = AnswerType.TEXT if form[f'dropdown-{qid}'] == 'text' else AnswerType.MCQ
        if new_type != question.answer_type and has_answers:
            if request.form.get(f'confirm_delete_answers_{qid}') != 'true':
                flash(f'Cannot change question type for "{question.question}" as it has existing answers. '
                      'Check "Delete existing answers" to proceed.', 'danger')
                return  # leave this question's type, options and file unchanged
            # Old answers do not fit the new type: delete them and ask again.
            distribution.reopen_question(question)
            flash(f'Existing answers for question "{question.question}" were deleted due to type change. '
                  'Its tasks will be answered again.', 'info')
        question.answer_type = new_type

        if new_type == AnswerType.TEXT:
            for i in range(1, 7):
                setattr(question, f'option{i}', None)
            question.number_of_options = 0
            question.answer = form.get(f'answer-{qid}', '')
        else:
            question.number_of_options = int(request.form.get(f'number_of_options-{qid}', 0))
            for i in range(1, 7):
                setattr(question, f'option{i}', form.get(f'mcq-option-{i}-{qid}'))  # None clears a removed option
            question.answer = form.get(f'mcq-correct-answer-{qid}')

    filename = save_attachment(files.get(f'questionfile-{qid}'))
    if filename:
        question.file_path = filename


def _add_question(project):
    kind = request.form.get('new_question_type', 'text')
    question = Question(
        question=request.form.get('new_question_text', 'New Question'),
        project_id=project.id,
        answer_type=AnswerType.TEXT if kind == 'text' else AnswerType.MCQ,
    )
    if kind == 'mcq':
        question.number_of_options = int(request.form.get('new_question_num_options', 0))
        for i in range(1, question.number_of_options + 1):
            setattr(question, f'option{i}', request.form.get(f'new_question_option_{i}', ''))
    db.session.add(question)
    db.session.commit()
    flash('New question added!', 'success')

    # A new question creates new tasks for every item; existing answers stay.
    if project.is_annotation_project:
        flash_distribution(distribution.distribute(project))
    else:
        _redistribute_survey(project)
    return redirect(url_for('manager.edit_project_questions', project_id=project.id))


def _delete_question(project):
    question = db.session.get(Question, request.form.get('question_id_to_delete'))
    if not question or question.project_id != project.id:
        flash('Error deleting question.', 'danger')
        return redirect(url_for('manager.edit_project_questions', project_id=project.id))

    QuestionAnswer.query.filter_by(question_id=question.id).delete()
    if project.is_annotation_project:
        for task in AnnotationTask.query.filter_by(question_id=question.id).all():
            TaskAssignment.query.filter_by(task_id=task.id).delete()
            db.session.delete(task)
    UserQuestionAssignment.query.filter_by(question_id=question.id).delete()
    db.session.delete(question)
    db.session.commit()
    flash('Question deleted.', 'success')

    # Only the deleted question's tasks are gone; rebalance what is left.
    if project.is_annotation_project:
        distribution.rebalance(project)
    else:
        _redistribute_survey(project)
    return redirect(url_for('manager.edit_project_questions', project_id=project.id))


def _update_settings(project):
    name = (request.form.get('project_name') or '').strip()
    if name:
        project.name = name
    if 'project_desc' in request.form:
        project.desc = request.form['project_desc']
    if project.is_annotation_project:
        project.questions_per_image = int(request.form.get('questions_per_image', project.questions_per_image))
        project.max_questions_per_user = int(request.form.get('max_questions_per_user', project.max_questions_per_user))
        project.answers_per_task = int(request.form.get('answers_per_task', 1))
        if 'hand_out' in request.form:
            project.hand_out_per_item = request.form['hand_out'] == 'item'
    db.session.commit()
    flash('Project settings updated successfully!', 'success')
    if project.is_annotation_project:
        flash_distribution(distribution.apply_answers_per_task(project).merge(distribution.distribute(project)))
    return redirect(url_for('manager.project_dashboard', project_id=project.id))


def _redistribute_survey(project):
    for group_project in project.assigned_groups:
        if group_project.group:
            distribute_questions_to_group(group_project.group, project)


_EDIT_ACTIONS = {
    'update_questions': _update_questions,
    'add_question': _add_question,
    'delete_question': _delete_question,
    'update_project_settings': _update_settings,
}
