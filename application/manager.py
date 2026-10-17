"""Project managers ("project-admin" role): their projects, dashboards, managers,
groups, results and question editing. System admins can open these pages too."""
import uuid

from flask import Blueprint, request, redirect, url_for, flash, render_template, session
from werkzeug.utils import secure_filename

from . import distribution
from .admin import upload_path
from .extns import db
from .feedback import flash_distribution
from .groups import distribute_questions_to_group
from .models import (AnnotationTask, AnswerType, Project, ProjectAdmin, ProjectImage, Question,
                     QuestionAnswer, TaskAssignment, User, UserQuestionAssignment)
from .permissions import admin_required, is_project_admin, project_admin_access_required, project_admin_required

manager = Blueprint("manager", __name__, url_prefix="/admin")


@manager.route("/my-projects")
@project_admin_access_required
def my_projects():
    user_id = session['id']
    # Get ProjectAdmin entries for the current user
    project_admin_entries = ProjectAdmin.query.filter_by(user_id=user_id).all()
    # Extract the Project objects from these entries
    projects = [entry.project for entry in project_admin_entries if entry.project]
    return render_template("admin/my_projects.html", projects=projects)

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
    
    # Get project admins
    project_admins = ProjectAdmin.query.filter_by(project_id=project_id).all()
    
    return render_template(
        "admin/project_dashboard.html",
        project=project,
        questions_count=questions_count,
        annotation_stats=annotation_stats,
        assigned_groups=assigned_groups,
        project_admins=project_admins
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
    db.session.add(ProjectAdmin(user_id=user.id, project_id=project.id, assigned_by=session['id']))
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
    current_user = db.session.get(User, session['id'])
    # Only system admins or this project's managers can add a manager
    if not (current_user.role == 'admin' or is_project_admin(project_id)):
        flash('You do not have permission to add project managers.', 'danger')
        return redirect(url_for('manager.project_admins', project_id=project_id))
    flash(*add_manager(project, _chosen_user(request.form)))
    return redirect(url_for('manager.project_admins', project_id=project_id))


@manager.route("/managers")
@admin_required
def managers():
    """Every project with its manager, and a form to add one, in one place."""
    projects = Project.query.order_by(Project.name).all()
    by_project = {pa.project_id: pa for pa in ProjectAdmin.query.all()}
    managing = {pa.user_id for pa in by_project.values()}
    without_project = [u for u in User.query.filter_by(role='project-admin').order_by(User.full_name)
                       if u.id not in managing]
    return render_template("admin/managers.html", projects=projects, by_project=by_project,
                           candidates=manager_candidates(), without_project=without_project)


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
    current_user_id = session['id']
    current_user = db.session.get(User, current_user_id)
    
    # Ensure the project admin being removed is for the current project
    if project_admin.project_id != project_id:
        flash('Invalid project admin.', 'danger')
        return redirect(url_for('manager.project_admins', project_id=project_id))
    
    # Check if current user has permission to remove this project admin
    # System admins can remove any project admin
    # Project admins can only remove project admins they assigned or other project admins (but not themselves)
    if current_user.role != 'admin':
        # Project admins can't remove themselves
        if project_admin.user_id == current_user_id:
            flash('You cannot remove yourself as a project admin.', 'danger')
            return redirect(url_for('manager.project_admins', project_id=project_id))
        
        # Project admins can only remove project admins they assigned or other project admins
        # (System admins can remove any project admin)
        if project_admin.assigned_by != current_user_id:
            # Check if the user being removed was assigned by a system admin
            assigner = db.session.get(User, project_admin.assigned_by)
            if assigner and assigner.role == 'admin':
                flash('You cannot remove a project admin assigned by a system admin.', 'danger')
                return redirect(url_for('manager.project_admins', project_id=project_id))
    
    # Get the user whose project admin role is being removed
    user = db.session.get(User, project_admin.user_id)
    
    # Remove the specific project admin assignment
    db.session.delete(project_admin)
    db.session.commit()

    # Check if the user is still a project admin for any other project
    remaining_project_roles = ProjectAdmin.query.filter_by(user_id=user.id).first()
    if user and not remaining_project_roles and user.role == 'project-admin':
        # If no other project roles, change role back to end-user
        user.role = 'end-user'
        db.session.commit()
    
    flash('Project admin removed successfully.', 'success')
    return redirect(url_for('manager.project_admins', project_id=project_id))

@manager.route("/project/<int:project_id>/admins/create", methods=['GET', 'POST'])
@project_admin_required
def create_project_admin(project_id):
    """Create a new user and assign them as a project admin"""
    project = db.get_or_404(Project, project_id)
    
    # Verify the current user is either a system admin or a project admin for this project
    current_user_id = session['id']
    current_user = db.session.get(User, current_user_id)
    
    # Only system admins or project admins for this project can add other project admins
    if not (current_user.role == 'admin' or is_project_admin(project_id)):
        flash('You do not have permission to add project admins.', 'danger')
        return redirect(url_for('manager.project_admins', project_id=project_id))
    
    if request.method == 'POST':
        fullname = request.form.get('fullname')
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Check if email already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('A user with this email already exists.', 'danger')
            return redirect(url_for('manager.create_project_admin', project_id=project_id))
        
        # Check if a project admin already exists for this project
        existing_project_manager = ProjectAdmin.query.filter_by(project_id=project_id).first()
        if existing_project_manager:
            flash('This project already has a manager. Only one manager is allowed per project.', 'danger')
            return redirect(url_for('manager.project_admins', project_id=project_id))

        # Create new user and assign as project admin
        new_user = User()
        new_user.register_project_admin(fullname, email, password, project_id, current_user_id)
        
        flash('New project admin created successfully.', 'success')
        return redirect(url_for('manager.project_admins', project_id=project_id))
    
    return render_template('admin/create_project_admin.html', project=project)

@manager.route("/project/<int:project_id>/groups")
@project_admin_required
def project_groups(project_id):
    project = db.get_or_404(Project, project_id)
    # In a real implementation, you'd fetch groups assigned to this project
    assigned_groups = [gp.group for gp in project.assigned_groups]
    return render_template("admin/project_groups.html", project=project, assigned_groups=assigned_groups)

@manager.route("/project/<int:project_id>/results")
@project_admin_required
def project_results(project_id):
    project = db.get_or_404(Project, project_id)
    # In a real implementation, you'd fetch and process results for this project
    return render_template("admin/project_results.html", project=project)

# New route for Project Admins to edit questions
@manager.route("/project/<int:project_id>/edit-questions", methods=['GET', 'POST'])
@project_admin_required
def edit_project_questions(project_id):
    project = db.get_or_404(Project, project_id)

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_questions':
            project_data = request.form.to_dict(flat=True)
            files = request.files.to_dict(flat=True)

            # Settings have their own form ("Update Settings"); this form never sends them.
            for key, value in project_data.items():
                if key.startswith('question-'):
                    question_id = int(key.split('-')[1])
                    question = db.session.get(Question, question_id)
                    if question:
                        if value.strip() != question.question.strip() and QuestionAnswer.query.filter_by(question_id=question_id).count():
                            flash(f'"{question.question}" was reworded to "{value}". Its existing answers were given to the old '
                                  'wording and are kept as they are. If the meaning changed, add a new question instead.', 'warning')
                        question.question = value

                        # Update answer type and options if submitted
                        old_answer_type = question.answer_type
                        answer_type_key = f'dropdown-{question_id}'
                        if answer_type_key in project_data:
                            new_answer_type_value = project_data[answer_type_key]
                            new_answer_type = AnswerType.TEXT if new_answer_type_value == 'text' else AnswerType.MCQ

                            # Check if question type is being changed and if there are existing answers
                            if old_answer_type != new_answer_type:
                                existing_answers_count = QuestionAnswer.query.filter_by(question_id=question_id).count()
                                if existing_answers_count > 0:
                                    if request.form.get(f'confirm_delete_answers_{question_id}') != 'true':
                                        flash(f'Cannot change question type for "{question.question}" as it has existing answers. Check "Delete existing answers" to proceed.', 'danger')
                                        continue  # leave this question's type and options unchanged
                                    # Old answers do not fit the new type: delete them and ask again.
                                    distribution.reopen_question(question)
                                    flash(f'Existing answers for question "{question.question}" were deleted due to type change. Its tasks will be answered again.', 'info')

                            # Proceed with updating answer type and options
                            question.answer_type = new_answer_type

                            # Clear existing MCQ options if changing to text
                            if question.answer_type == AnswerType.TEXT:
                                for i in range(1, 7): # Assuming max 6 options
                                    setattr(question, f'option{i}', None)
                                question.number_of_options = 0
                                question.answer = project_data.get(f'answer-{question_id}', '') # Update text answer

                            # Update MCQ options if changing to mcq or staying as mcq
                            elif question.answer_type == AnswerType.MCQ:
                                question.number_of_options = int(request.form.get(f'number_of_options-{question_id}', 0))
                                for i in range(1, 7): # Assuming max 6 options
                                    option_key = f'mcq-option-{i}-{question_id}'
                                    if option_key in project_data:
                                        setattr(question, f'option{i}', project_data[option_key])
                                    else:
                                        setattr(question, f'option{i}', None) # Clear if option removed
                                # For MCQ, get the correct answer from the dropdown
                                mcq_answer_key = f'mcq-correct-answer-{question_id}'
                                if mcq_answer_key in project_data:
                                    question.answer = project_data[mcq_answer_key]
                                else:
                                    question.answer = None

                        # Handle file uploads
                        file_key = f'questionfile-{question_id}'
                        if file_key in files and files[file_key].filename != '':
                             file = files[file_key]
                             filename = str(uuid.uuid4()) + secure_filename(file.filename)
                             file.save(upload_path(filename))
                             question.file_path = filename


            db.session.commit()
            flash('Project questions and settings updated successfully!', 'success')
            if project.is_annotation_project:
                flash_distribution(distribution.apply_answers_per_task(project).merge(distribution.distribute(project)))
            return redirect(url_for('manager.project_dashboard', project_id=project_id))

        elif action == 'add_question':
            # Enhanced logic for adding a new question
            new_question_text = request.form.get('new_question_text', 'New Question')
            new_question_type = request.form.get('new_question_type', 'text')
            
            question = Question(
                question=new_question_text,
                project_id=project.id,
                answer_type=AnswerType.TEXT if new_question_type == 'text' else AnswerType.MCQ
            )
            
            # If it's an MCQ, handle the options
            if new_question_type == 'mcq':
                num_options = int(request.form.get('new_question_num_options', 0))
                question.number_of_options = num_options
                
                for i in range(1, num_options + 1):
                    option_value = request.form.get(f'new_question_option_{i}', '')
                    setattr(question, f'option{i}', option_value)
            
            db.session.add(question)
            db.session.commit()
            flash('New question added!', 'success')
            
            # A new question creates new tasks for every image; existing answers stay.
            if project.is_annotation_project:
                flash_distribution(distribution.distribute(project))
            else: # For survey projects
                for group_project in project.assigned_groups:
                    if group_project.group:
                        distribute_questions_to_group(group_project.group, project)

            # Redirect back to the edit page to see the new question
            return redirect(url_for('manager.edit_project_questions', project_id=project.id))

        elif action == 'delete_question':
            question_id_to_delete = request.form.get('question_id_to_delete')
            question = db.session.get(Question, question_id_to_delete)
            if question and question.project_id == project.id:
                # Delete related answers first
                QuestionAnswer.query.filter_by(question_id=question.id).delete()
                
                # Delete related annotation tasks if this is an annotation project
                if project.is_annotation_project:
                    tasks = AnnotationTask.query.filter_by(question_id=question.id).all()
                    for task in tasks:
                        # Delete task assignments
                        TaskAssignment.query.filter_by(task_id=task.id).delete()
                        db.session.delete(task)
                
                # Delete user question assignments for this question
                UserQuestionAssignment.query.filter_by(question_id=question.id).delete()

                db.session.delete(question)
                db.session.commit()
                flash('Question deleted.', 'success')
                
                # Only the deleted question's tasks are gone; rebalance what is left.
                if project.is_annotation_project:
                    distribution.rebalance(project)
                else: # For survey projects
                    for group_project in project.assigned_groups:
                        if group_project.group:
                            distribute_questions_to_group(group_project.group, project)
            else:
                flash('Error deleting question.', 'danger')
            # Redirect back to the edit page
            return redirect(url_for('manager.edit_project_questions', project_id=project.id))
        
        elif action == 'update_project_settings':
            # Update project settings only
            if project.is_annotation_project:
                project.questions_per_image = int(request.form.get('questions_per_image', project.questions_per_image))
                project.max_questions_per_user = int(request.form.get('max_questions_per_user', project.max_questions_per_user))
                project.answers_per_task = int(request.form.get('answers_per_task', 1)) # Default to 1 for single completion
                if 'hand_out' in request.form:
                    project.hand_out_per_item = request.form['hand_out'] == 'item'
                db.session.commit()
                flash('Project settings updated successfully!', 'success')
                flash_distribution(distribution.apply_answers_per_task(project).merge(distribution.distribute(project)))
            return redirect(url_for('manager.project_dashboard', project_id=project_id))

    return render_template("admin/edit_project_questions.html", project=project)
