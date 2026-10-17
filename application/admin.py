from flask import send_file, make_response
import csv
import io
import os
import uuid
import zipfile
import secrets
import logging

from flask import Blueprint, request, redirect, url_for, flash, render_template, session, abort
from sqlalchemy import delete
from werkzeug.utils import secure_filename

from .extns import db
from .models import Project, Question, User, AnswerType, QuestionAnswer, UserGroup, UserGroupMembership, GroupProject, UserQuestionAssignment, ProjectImage, AnnotationTask, TaskAssignment, ProjectAdmin, ExportLog, media_type_for
from .auth import MIN_PASSWORD_LENGTH
from .jinja_utils import plural
from . import distribution
from .feedback import flash_distribution, warn_idle_members
from .groups import assign_questions_to_new_group_member, distribute_questions_to_group
from . import export as dataset_export
from .permissions import admin_required, is_project_admin, project_admin_access_required, project_admin_required

admin = Blueprint("admin", __name__, url_prefix="/admin")
log = logging.getLogger(__name__)

def upload_dir(*parts):
    """Create (if needed) and return a folder inside the upload root.

    The upload root is ``static/uploads`` unless UPLOAD_ROOT is configured.
    """
    from flask import current_app
    root = current_app.config.get("UPLOAD_ROOT") or os.path.join(current_app.static_folder, "uploads")
    path = os.path.join(root, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def upload_path(filename):
    """Absolute path for a file stored directly in the upload root."""
    return os.path.join(upload_dir(), filename)


def public_file_url(filename):
    return url_for("static", filename=f"uploads/{filename}", _external=True)


@admin.route("/dashboard")
@admin_required
def dashboard():
    from sqlalchemy import func
    from datetime import datetime, timedelta

    total_projects = Project.query.count()
    total_users = User.query.filter_by(role='end-user').count()
    total_groups = UserGroup.query.count()

    # Get weekly answer statistics for the last 12 weeks
    weeks_data = []
    for i in range(12):
        week_start = datetime.now() - timedelta(weeks=i+1)
        week_end = datetime.now() - timedelta(weeks=i)

        answers_count = QuestionAnswer.query.filter(
            QuestionAnswer.submitted_date >= week_start,
            QuestionAnswer.submitted_date < week_end
        ).count()

        weeks_data.append({
            'week': f"Week {12-i}",
            'answers': answers_count
        })

    # Reverse to show oldest to newest
    weeks_data.reverse()

    # Group progress: the share of this group's work that is done, counting
    # annotation tasks (finished assignments) and survey questions (answered).
    group_stats = []
    for group in UserGroup.query.all():
        member_ids = [m.user_id for m in group.members]
        project_ids = [gp.project_id for gp in group.assigned_projects]
        tasks = TaskAssignment.query.join(AnnotationTask).filter(
            TaskAssignment.user_id.in_(member_ids), AnnotationTask.project_id.in_(project_ids))
        survey = UserQuestionAssignment.query.filter(
            UserQuestionAssignment.user_id.in_(member_ids),
            UserQuestionAssignment.group_project_id.in_([f"{group.id}-{pid}" for pid in project_ids]))
        total = tasks.count() + survey.count()
        done = tasks.filter(TaskAssignment.is_completed.is_(True)).count() + sum(
            1 for q in survey if QuestionAnswer.query.filter_by(
                user_id=q.user_id, question_id=q.question_id, task_assignment_id=None).first())
        group_stats.append({
            'name': group.name,
            'members': len(member_ids),
            'projects': len(project_ids),
            'completion_rate': round(done / total * 100, 1) if total else 0,
        })

    return render_template('admin/dashboard.html',
                         total_projects=total_projects,
                         total_users=total_users,
                         total_groups=total_groups,
                         weeks_data=weeks_data,
                         group_stats=group_stats)

@admin.route("/users")
@admin_required
def users():
    users_data = []
    end_users = User.query.filter_by(role='end-user').all()

    for user in end_users:
        assigned_projects = set()

        # 1. Get projects directly assigned to the user (if applicable)
        # This part might be for non-annotation projects or older assignments
        direct_project_assignments = UserQuestionAssignment.query.filter_by(user_id=user.id).all()
        for assignment in direct_project_assignments:
            question = db.session.get(Question, assignment.question_id)
            if question and question.belongs_to:
                assigned_projects.add(question.belongs_to.id)
        
        # 2. Get projects assigned through groups
        user_group_memberships = UserGroupMembership.query.filter_by(user_id=user.id).all()
        for membership in user_group_memberships:
            group_projects = GroupProject.query.filter_by(group_id=membership.group_id).all()
            for gp in group_projects:
                if gp.project:
                    assigned_projects.add(gp.project.id)
        
        # 3. Get projects assigned through TaskAssignment for annotation projects
        # This is crucial for annotation projects since tasks are assigned, not questions directly
        annotation_task_assignments = TaskAssignment.query.filter_by(user_id=user.id).all()
        for task_assign in annotation_task_assignments:
            if task_assign.task and task_assign.task.project:
                assigned_projects.add(task_assign.task.project.id)

        users_data.append({
            'user': user,
            'assigned_projects_count': len(assigned_projects),
            'assigned_groups': [m.group for m in user_group_memberships if m.group]
        })
    return render_template('admin/users.html', users_data=users_data)

@admin.route("/create-user", methods=['GET', 'POST'])
@admin_required
def create_user():
    """Add one annotator by hand, optionally straight into a group."""
    groups = UserGroup.query.order_by(UserGroup.name).all()
    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        if not full_name or not email:
            flash('Please enter a name and an email address.', 'danger')
            return render_template('admin/create_user.html', groups=groups, form=request.form)
        if User.query.filter_by(email=email).first():
            flash(f'A user with the email {email} already exists.', 'danger')
            return render_template('admin/create_user.html', groups=groups, form=request.form)
        if password and len(password) < MIN_PASSWORD_LENGTH:
            flash(f'The password must be at least {MIN_PASSWORD_LENGTH} characters long.', 'danger')
            return render_template('admin/create_user.html', groups=groups, form=request.form)

        temporary = not password
        if temporary:
            password = secrets.token_urlsafe(8)
        user = User()
        user.register_end_user(full_name, email, password)
        db.session.commit()
        if temporary:
            flash(f'{email} was given the temporary password {password}. '
                  'Share it with them and ask them to change it after signing in.', 'warning')

        group = db.session.get(UserGroup, request.form.get('group_id', type=int) or 0)
        if group:
            db.session.add(UserGroupMembership(user_id=user.id, group_id=group.id))
            db.session.commit()
            result = assign_questions_to_new_group_member(user, group)
            flash_distribution(result, f'{full_name} was created and added to group "{group.name}".')
            for project in distribution.projects_for_group(group.id):
                warn_idle_members(project, [user])
        else:
            flash(f'{full_name} was created.', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/create_user.html', groups=groups, form={})


@admin.route("/import-users", methods=['GET', 'POST'])
@admin_required
def import_users():
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file part', 'danger')
            return redirect(request.url)
        
        csv_file = request.files['csv_file']
        if csv_file.filename == '':
            flash('No selected file', 'danger')
            return redirect(request.url)
        
        if not csv_file.filename.lower().endswith('.csv'):
            flash('Invalid file type. Please upload a CSV file.', 'danger')
            return redirect(request.url)

        stream = io.StringIO(csv_file.stream.read().decode("UTF8"))
        csv_reader = csv.reader(stream)
        header = next(csv_reader, None) # Skip header row

        if not header or not all(col in header for col in ['full_name', 'email', 'password']):
            flash('CSV must contain "full_name", "email", and "password" columns.', 'danger')
            return redirect(request.url)

        full_name_idx = header.index('full_name')
        email_idx = header.index('email')
        password_idx = header.index('password')

        users_created = 0
        users_skipped_duplicate = 0
        users_skipped_invalid = 0
        errors = []
        users_to_assign = [] # Collect users (new and existing) for assignment

        # Store the original stream position to reset it for the second pass if needed
        # This is not needed if we convert csv_reader to a list.
        # original_stream_pos = csv_file.stream.tell()

        # To handle the csv_reader being an iterator, we'll store the rows in a list
        # and then process them. For very large files, this might need optimization
        # (e.g., processing in chunks or using a temporary file).
        # For now, assuming CSVs are not excessively large.
        csv_file.stream.seek(0) # Reset stream position to the beginning
        stream_for_processing = io.StringIO(csv_file.stream.read().decode("UTF8"))
        csv_reader_for_processing = csv.reader(stream_for_processing)
        next(csv_reader_for_processing, None) # Skip header row again

        for i, row in enumerate(csv_reader_for_processing): # Use the new reader for processing
            try:
                full_name = row[full_name_idx].strip()
                email = row[email_idx].strip()
                password = row[password_idx].strip() if password_idx < len(row) else ''

                if not full_name or not email:
                    users_skipped_invalid += 1
                    errors.append(f"Row {i+2}: Missing full name or email. Skipped.")
                    continue

                existing_user = User.query.filter_by(email=email).first()
                if existing_user:
                    users_skipped_duplicate += 1
                    errors.append(f"Row {i+2}: User with email '{email}' already exists. Skipped creation, but will attempt assignment.")
                    users_to_assign.append(existing_user) # Add existing user for potential assignment
                    continue
                
                # Missing or too-short passwords are replaced with a random one.
                # The admin must see it, otherwise the user could never sign in.
                if not password or len(password) < MIN_PASSWORD_LENGTH:
                    reason = "had no password" if not password else f"had a password shorter than {MIN_PASSWORD_LENGTH} characters"
                    password = secrets.token_urlsafe(8)
                    errors.append(
                        f"Row {i+2}: {email} {reason}, so a temporary password was created: {password} "
                        "Share it with them and ask them to change it after signing in."
                    )

                new_user = User()
                new_user.register_end_user(full_name, email, password)
                db.session.add(new_user) # Add to session
                db.session.flush() # Get ID for new_user before commit
                users_created += 1
                users_to_assign.append(new_user) # Add newly created user for assignment

            except IndexError:
                users_skipped_invalid += 1
                errors.append(f"Row {i+2}: Malformed row. Skipped.")
            except Exception as e:
                db.session.rollback() # Rollback any partial changes for this user
                users_skipped_invalid += 1
                errors.append(f"Row {i+2}: Error processing row: {str(e)}. Skipped.")
        
        db.session.commit() # Commit all successful user creations

        # Get selected group and project for assignment
        selected_group_id = request.form.get('user_group_id')
        selected_project_id = request.form.get('project_id')

        # Process assignments for all users (new and existing) that were successfully identified/created from CSV
        if selected_group_id:
            group = db.session.get(UserGroup, selected_group_id)
            if group:
                group_assignments_made = 0
                for user in users_to_assign:
                    existing_membership = UserGroupMembership.query.filter_by(user_id=user.id, group_id=group.id).first()
                    if not existing_membership:
                        membership = UserGroupMembership(user_id=user.id, group_id=group.id)
                        db.session.add(membership)
                        group_assignments_made += 1
                db.session.commit()
                if group_assignments_made > 0:
                    flash(f"{group_assignments_made} users assigned to group '{group.name}'.", 'success')
                else:
                    flash(f"All users from CSV are already in group '{group.name}'.", 'info')
            else:
                flash("Selected user group not found for assignment.", 'warning')

        if selected_project_id:
            project = db.session.get(Project, selected_project_id)
            if project:
                project_assignments_made = 0
                if project.is_annotation_project:
                    if AnnotationTask.query.filter_by(project_id=project.id).first():
                        users = {u.id: u for u in distribution.eligible_users(project)}
                        users.update({u.id: u for u in users_to_assign})
                        result = distribution.assign_open_slots(project, users=list(users.values()))
                        flash_distribution(result)
                        if not result.assignments_created:
                            flash(f"No open tasks were left to assign in '{project.name}'.", 'info')
                    else:
                        flash(f"No annotation tasks found for project '{project.name}'. Generate tasks first.", 'warning')
                else:
                    # For regular projects, assign questions directly to each user
                    for user in users_to_assign:
                        for question in project.questions:
                            existing_assignment = UserQuestionAssignment.query.filter_by(user_id=user.id, question_id=question.id).first()
                            if not existing_assignment:
                                assignment = UserQuestionAssignment(
                                    user_id=user.id,
                                    question_id=question.id,
                                    group_project_id=f"imported-{project.id}" # A unique identifier for imported users not tied to a group
                                )
                                db.session.add(assignment)
                                project_assignments_made += 1
                    db.session.commit()
                    if project_assignments_made > 0:
                        flash(f"{project_assignments_made} questions assigned to users for project '{project.name}'.", 'success')
                    else:
                        flash(f"All users from CSV are already assigned questions in project '{project.name}'.", 'info')
            else:
                flash("Selected project not found for assignment.", 'warning')

        summary_message = (f"CSV Import Complete: {plural(users_created, 'user')} created, "
                           f"{plural(users_skipped_duplicate, 'duplicate')} skipped, "
                           f"{plural(users_skipped_invalid, 'invalid row')} skipped.")
        flash(summary_message, 'info')
        for error in errors:
            flash(error, 'warning')

        return redirect(url_for('admin.users')) # Redirect to users list

    groups = UserGroup.query.all()
    projects = Project.query.all()
    return render_template('admin/import_users.html', groups=groups, projects=projects)

@admin.route("/create-project", methods=['GET', 'POST'])
@project_admin_access_required
def create_project():
    if request.method == 'POST':
        project_name = request.form.get("project_name")
        project_desc = request.form.get("project_desc")
        assigned_group_id = request.form.get("assigned_group_id")

        project = Project(name=project_name,
                          desc=project_desc)
        db.session.add(project)
        db.session.commit()

        # If the current user is a project-admin, assign them as an admin for this new project
        if session.get('role') == 'project-admin':
            current_user_id = session.get('id')
            if current_user_id:
                from .models import ProjectAdmin # Import locally to avoid circular dependency
                new_project_admin = ProjectAdmin(
                    user_id=current_user_id,
                    project_id=project.id,
                    assigned_by=current_user_id # Self-assigned
                )
                db.session.add(new_project_admin)
                # No commit here, will commit all at once later

        # Assign project to selected group (optional at creation)
        if assigned_group_id:
            group = db.session.get(UserGroup, assigned_group_id)
            if group:
                existing_assignment = GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first()
                if not existing_assignment:
                    group_project = GroupProject(group_id=group.id, project_id=project.id)
                    db.session.add(group_project)
                    # No task distribution here; tasks are generated after images are uploaded

        # Save questions if any are provided
        total_questions_str = request.form.get("total_questions")
        if total_questions_str and int(total_questions_str) > 0:
            total_questions = int(total_questions_str)
            for i in range(1, total_questions + 1):
                file_key = f'question-{i}-file'
                question_text = request.form.get(f"question-{i}")
                file_path = request.files.get(f"question-{i}-file")
                filename = None
                if file_path and file_path.filename:
                    filename = str(uuid.uuid4()) + \
                        secure_filename(file_path.filename)

                    file_path.save(upload_path(filename))

                answer_type_value = request.form.get(f"dropdown-{i}")
                answer_type = AnswerType.TEXT if answer_type_value == 'text' else AnswerType.MCQ
                number_of_options = int(request.form.get(f"number_of_options-{i}", 0))
                answer = request.form.get(f"answer-{i}")

                # Create and save question
                question = Question(
                    question=question_text,
                    file_path=filename,
                    project_id=project.id,
                    answer_type=answer_type,
                    number_of_options=number_of_options,
                    answer=answer
                )

                # Save MCQ options if applicable
                if answer_type == AnswerType.MCQ:
                    for opt_num in range(1, number_of_options + 1):
                        setattr(question, f"option{opt_num}", request.form.get(
                            f"mcq-option-{opt_num}-{i}"))

                db.session.add(question)
            db.session.commit() # Commit questions separately if needed, or after main project commit

        flash("Form data saved successfully!", "success")
        return redirect(url_for("admin.view_projects"))
    return render_template("admin/create_project.html")


@admin.route("/view-projects")
@admin_required
def view_projects():
    projects = Project.query.all()

    return render_template("admin/view_projects.html", projects=projects)


@admin.route("/delete-project/<int:project_id>", methods=["POST"])
@admin_required
def delete_project(project_id):
    project = db.get_or_404(Project, project_id)

    try:
        # Delete annotation-related data if this is an annotation project
        if project.is_annotation_project:
            # Delete task assignments first
            for task in project.annotation_tasks:
                for assignment in task.assignments:
                    # Delete answers linked to this assignment
                    answers = QuestionAnswer.query.filter_by(task_assignment_id=assignment.id).all()
                    for answer in answers:
                        db.session.delete(answer)
                    db.session.delete(assignment)
                db.session.delete(task)

            # Delete project images
            for image in project.images:
                # Delete image file from filesystem
                try:
                    if os.path.exists(image.file_path):
                        os.remove(image.file_path)
                except:
                    pass  # Continue even if file deletion fails
                db.session.delete(image)

        ExportLog.query.filter_by(project_id=project_id).delete()

        # Delete group project assignments
        group_projects = GroupProject.query.filter_by(project_id=project_id).all()
        for group_project in group_projects:
            # Delete user question assignments for this group project
            group_project_id = f"{group_project.group_id}-{group_project.project_id}"
            assignments = UserQuestionAssignment.query.filter_by(group_project_id=group_project_id).all()
            for assignment in assignments:
                db.session.delete(assignment)
            db.session.delete(group_project)

        # Delete regular question answers (not linked to task assignments)
        for question in project.questions:
            qas = QuestionAnswer.query.filter_by(
                question_id=question.id,
                task_assignment_id=None
            ).all()
            for qa in qas:
                db.session.delete(qa)

        # Delete questions
        delete_stmt = delete(Question).where(Question.project_id == project.id)
        db.session.execute(delete_stmt)

        # Finally delete the project
        db.session.delete(project)
        db.session.commit()

        flash("Project Deleted Successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting project: {str(e)}", "error")

    return redirect(url_for("admin.view_projects"))


@admin.route("/edit-project/<int:project_id>", methods=['GET', 'POST'])
@project_admin_required
def edit_project(project_id):
    project = db.get_or_404(Project, project_id)

    if request.method == 'POST':
        project_data = request.form.to_dict(flat=True)
        project_name = project_data.get('project-name')
        project_desc = project_data.get('project-description')
        log.debug(f"Edit Project - Received project_name: {project_name}, project_desc: {project_desc}")
        project.name = project_name
        project.desc = project_desc

        files = request.files.to_dict(flat=True)

        for key, file in files.items():
            if key.startswith('questionfile-'):
                question_id = int(key.split('-')[1])
                question = db.session.get(Question, question_id)
                if question:
                    file = request.files.get(key)
                    filename = None
                    if file:
                        filename = str(uuid.uuid4()) + \
                            secure_filename(file.filename)
                        file_path = upload_path(filename)
                        file.save(file_path)

                    if file.filename:
                        question.file_path = filename

        # Update annotation project settings if applicable
        if project.is_annotation_project:
            project.max_questions_per_user = int(request.form.get('max_questions_per_user', project.max_questions_per_user))
            project.answers_per_task = int(request.form.get('answers_per_task', project.answers_per_task))

        db.session.commit()
        flash('Project details updated successfully!', 'success')
        if project.is_annotation_project:
            flash_distribution(distribution.apply_answers_per_task(project).merge(distribution.distribute(project)))
        return redirect(url_for("admin.view_projects"))

    return render_template("admin/edit_project.html", project=project)


@admin.route("/view-questions/<int:project_id>")
@project_admin_required
def view_questions(project_id):
    project = db.get_or_404(Project, project_id)
    # This route is now for both System Admins and Project Admins to view questions
    return render_template("admin/view_questions.html", project=project)


# Individual project assignment removed - only group assignments supported


@admin.route("/download-files/<int:project_id>")
@project_admin_required
def download_files(project_id):
    project = db.session.get(Project, project_id)
    if project:
        output = io.StringIO()
        output.write(f"Project Name: {project.name}\n")
        output.write(f"Project Description: {project.desc}\n\n")

        for question in project.questions:
            output.write(f"  Question: {question.question}\n")
            file_path = "No File"
            if question.file_path:
                file_path = public_file_url(question.file_path)
            output.write(f"  Question File: {file_path}\n")

            question_answers = QuestionAnswer.query.filter_by(question_id=question.id)
            output.write("  User Answers:\n")
            for q_a in question_answers:
                output.write(f"    {q_a.user.full_name}: {q_a.answer}\n")
            output.write("\n")

        output.write("=" * 40 + "\n\n")  # Separator between projects

        # Convert StringIO to BytesIO for send_file
        mem = io.BytesIO()
        mem.write(output.getvalue().encode())
        mem.seek(0)

        response = make_response(send_file(
            mem, as_attachment=True, download_name='project-answers.txt', mimetype='text/plain'))
        return response
    else:
        flash('Project not found', 'danger')
        return redirect(url_for('admin.view_projects'))


# Download answers per user
@admin.route("/download-answers/<int:user_id>")
@admin_required
def download_answers(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found', 'danger')
        return redirect(url_for('admin.users'))

    # Fetch all answers by this user
    user_answers = QuestionAnswer.query.filter_by(user_id=user_id).order_by(QuestionAnswer.submitted_date).all()

    output = io.StringIO()
    writer = csv.writer(output)

    # CSV Header
    header = [
        'User Email',
        'User Name',
        'Project Name',
        'Project Type',
        'Question Text',
        'Answer',
        'Answer Type',
        'Image Filename',
        'Submitted Date',
        'Is Completed Assignment'
    ]
    writer.writerow(header)

    if user_answers:
        for answer_obj in user_answers:
            question = answer_obj.question
            project = None
            image_filename = 'N/A'
            is_completed_assignment = False

            if answer_obj.task_assignment_id: # This answer is linked to an annotation task assignment
                task_assignment = db.session.get(TaskAssignment, answer_obj.task_assignment_id)
                if task_assignment and task_assignment.task: # Ensure task and project exist
                    project = task_assignment.task.project
                    if task_assignment.task.image:
                        image_filename = task_assignment.task.image.original_filename
                    is_completed_assignment = task_assignment.is_completed
            elif question and question.belongs_to: # This is a regular question directly linked to a project
                project = question.belongs_to
                # For regular questions, there's no direct image association in this context
            
            project_name = project.name if project else 'N/A'
            project_type = 'Annotation' if (project and project.is_annotation_project) else 'Regular'
            question_text = question.question if question else 'N/A'
            answer_text = answer_obj.answer
            answer_type = question.answer_type.name if question and question.answer_type else 'N/A'
            submitted_date = answer_obj.submitted_date.strftime('%Y-%m-%d %H:%M:%S') if answer_obj.submitted_date else 'N/A'

            writer.writerow([
                user.email,
                user.full_name,
                project_name,
                project_type,
                question_text,
                answer_text,
                answer_type,
                image_filename,
                submitted_date,
                'Yes' if is_completed_assignment else 'No'
            ])
    else:
        writer.writerow([user.email, user.full_name, '', '', 'No answers found for this user', '', '', '', '', ''])

    mem = io.BytesIO(output.getvalue().encode('utf-8'))
    return make_response(send_file(
        mem,
        as_attachment=True,
        download_name=f'user_{user.id}_answers.csv',
        mimetype='text/csv'
    ))


@admin.route("/profile", methods=['GET', 'POST'])
@admin_required
def admin_profile():
    admin_user = db.session.get(User, session['id'])

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_email':
            new_email = request.form.get('email')
            if not new_email:
                flash('Email cannot be empty', 'danger')
                return redirect(url_for('admin.admin_profile'))

            # Check if email already exists
            existing = User.query.filter_by(email=new_email).first()
            if existing and existing.id != admin_user.id:
                flash('Email already exists', 'danger')
                return redirect(url_for('admin.admin_profile'))

            admin_user.email = new_email
            db.session.commit()
            flash('Email updated successfully', 'success')

        elif action == 'update_password':
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if not admin_user.check_password(current_password):
                flash('Current password is incorrect', 'danger')
                return redirect(url_for('admin.admin_profile'))

            if new_password != confirm_password:
                flash('New passwords do not match', 'danger')
                return redirect(url_for('admin.admin_profile'))

            if len(new_password) < 6:
                flash('Password must be at least 6 characters long', 'danger')
                return redirect(url_for('admin.admin_profile'))

            admin_user.set_password(new_password)
            db.session.commit()
            flash('Password updated successfully', 'success')

        return redirect(url_for('admin.admin_profile'))

    return render_template('admin/profile.html', admin=admin_user)
    
#Download answers per projects by user
@admin.route("/download-full")
@admin_required
def full_download():
    projects = Project.query.all()
    output = io.StringIO()

    for p in projects:
        output.write(f"Project Name: {p.name}\n")
        output.write(f"Project Description: {p.desc}\n\n")

        for question in p.questions:
            output.write(f"  Question: {question.question}\n")
            file_path = "No File"
            if question.file_path:
                file_path = public_file_url(question.file_path)
            output.write(f"  Question File: {file_path}\n")

            question_answers = QuestionAnswer.query.filter_by(question_id=question.id)
            output.write("  User Answers:\n")
            for q_a in question_answers:
                output.write(f"    {q_a.user.full_name}: {q_a.answer}\n")
            output.write("\n")

        output.write("=" * 40 + "\n\n")  # Separator between projects

    output.seek(0)

    # Convert StringIO to BytesIO for send_file
    mem = io.BytesIO()
    mem.write(output.getvalue().encode())
    mem.seek(0)

    response = make_response(send_file(
        mem,
        as_attachment=True,
        download_name='user_answers.txt',
        mimetype='text/plain'
    ))
    return response


# ============================================================================
# IMAGE ANNOTATION PROJECT ROUTES
# ============================================================================

@admin.route("/create-annotation-project", methods=["GET", "POST"])
@project_admin_access_required
def create_annotation_project():
    """Create a new image annotation project with customizable questions"""
    if request.method == "POST":
        project_name = request.form.get("project_name")
        project_desc = request.form.get("project_desc")
        max_questions_per_user = int(request.form.get("max_questions_per_user", 100))
        answers_per_task = max(1, int(request.form.get("answers_per_task", 1)))
        total_questions = int(request.form.get("total_questions", 0))
        assigned_group_id = request.form.get("assigned_group_id")

        # Create the annotation project
        project = Project(
            name=project_name,
            desc=project_desc,
            user_id=session["id"],
            is_annotation_project=True,
            max_questions_per_user=max_questions_per_user,
            answers_per_task=answers_per_task,
            questions_per_image=total_questions, # Set questions_per_image based on total_questions from form
            hand_out_per_item=request.form.get("hand_out") == "item",
        )
        db.session.add(project)
        db.session.flush() # Get the project ID

        # If the current user is a project-admin, assign them as an admin for this new project
        if session.get('role') == 'project-admin':
            current_user_id = session.get('id')
            if current_user_id:
                from .models import ProjectAdmin # Import locally to avoid circular dependency
                new_project_admin = ProjectAdmin(
                    user_id=current_user_id,
                    project_id=project.id,
                    assigned_by=current_user_id # Self-assigned
                )
                db.session.add(new_project_admin)
                # No commit here, will commit all at once later

        # Assign project to selected group (optional at creation)
        if assigned_group_id:
            group = db.session.get(UserGroup, assigned_group_id)
            if group:
                existing_assignment = GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first()
                if not existing_assignment:
                    group_project = GroupProject(group_id=group.id, project_id=project.id)
                    db.session.add(group_project)
                    # No task distribution here; tasks are generated after images are uploaded

        # Create the questions. Each can be free text or multiple choice
        # (multiple choice makes answers comparable, see the agreement score).
        if total_questions > 0:
            parsed = []
            for i in range(1, total_questions + 1):
                text = (request.form.get(f"question_{i}") or "").strip()
                if not text:
                    continue
                kind = request.form.get(f"question_type_{i}", "text")
                options = [o.strip() for o in (request.form.get(f"question_options_{i}") or "").splitlines() if o.strip()]
                if kind == "mcq" and not 2 <= len(options) <= 6:
                    flash(f'Question {i} is multiple choice, so it needs between 2 and 6 options, one per line.', "error")
                    db.session.rollback()
                    return redirect(request.url)
                parsed.append((text, kind, options))

            if len(parsed) != total_questions:
                flash(f"Please provide exactly {total_questions} questions!", "error")
                db.session.rollback()
                return redirect(request.url)

            for text, kind, options in parsed:
                question = Question(
                    question=text,
                    answer_type=AnswerType.MCQ if kind == "mcq" else AnswerType.TEXT,
                    number_of_options=len(options) if kind == "mcq" else 0,
                    project_id=project.id,
                )
                for n, option in enumerate(options if kind == "mcq" else [], start=1):
                    setattr(question, f"option{n}", option[:50])
                db.session.add(question)

        db.session.commit() # Commit all changes related to project and questions
        flash(f"Annotation project '{project_name}' created successfully!", "success")
        return redirect(url_for("admin.upload_images", project_id=project.id))

    groups = UserGroup.query.all()
    return render_template("admin/create_annotation_project.html", groups=groups)


@admin.route("/upload-images/<int:project_id>", methods=["GET", "POST"])
@project_admin_required
def upload_images(project_id):
    """Upload a ZIP of images and/or audio clips for an annotation project."""
    project = db.get_or_404(Project, project_id)

    if not project.is_annotation_project:
        flash("This is not an annotation project!", "error")
        return redirect(url_for("admin.view_projects"))

    if request.method == "POST":
        # One field takes ZIP files and/or loose image and audio files.
        uploads = [f for f in request.files.getlist('image_zip') if f and f.filename]
        if not uploads:
            flash("No file selected!", "error")
            return redirect(request.url)

        upload_dir_path = upload_dir('images', str(project_id))
        existing_names = {
            img.original_filename
            for img in ProjectImage.query.filter_by(project_id=project_id).all()
        }
        saved = {"image": 0, "audio": 0}
        duplicates_skipped = 0
        unsupported = []

        def save(original_filename, read):
            """Store one item unless its name is already in the project."""
            nonlocal duplicates_skipped
            media_type = media_type_for(original_filename)
            if original_filename in existing_names:
                duplicates_skipped += 1
                return
            unique_filename = f"{uuid.uuid4()}_{secure_filename(original_filename)}"
            final_path = os.path.join(upload_dir_path, unique_filename)
            with open(final_path, 'wb') as dst:
                dst.write(read())
            db.session.add(ProjectImage(
                project_id=project_id,
                filename=unique_filename,
                original_filename=original_filename,
                file_path=final_path,
                media_type=media_type,
            ))
            existing_names.add(original_filename)
            saved[media_type] += 1

        for upload in uploads:
            if upload.filename.lower().endswith('.zip'):
                try:
                    with zipfile.ZipFile(upload, 'r') as zip_ref:
                        for file_info in zip_ref.infolist():
                            original_filename = os.path.basename(file_info.filename)
                            # Nested folders are flattened; junk and unsupported files are ignored.
                            if (file_info.is_dir()
                                    or file_info.filename.startswith('__MACOSX/')
                                    or original_filename.startswith('.')
                                    or media_type_for(original_filename) is None):
                                continue
                            with zip_ref.open(file_info) as src:
                                save(original_filename, src.read)
                except zipfile.BadZipFile:
                    flash(f"{upload.filename} is not a valid ZIP archive, so it was skipped.", "error")
            else:
                original_filename = os.path.basename(upload.filename)
                if media_type_for(original_filename) is None or original_filename.startswith('.'):
                    unsupported.append(original_filename)
                    continue
                save(original_filename, upload.read)

        db.session.commit()
        if unsupported:
            flash(f"Skipped files that are not images, audio or ZIP: {', '.join(unsupported)}.", "warning")
        if duplicates_skipped:
            flash(f"Skipped {plural(duplicates_skipped, 'file')} already in this project (same file name).", "warning")
        if not saved["image"] and not saved["audio"]:
            flash("The upload contained no new images or audio files.", "warning")
            return redirect(request.url)
        parts = []
        if saved["image"]:
            parts.append(plural(saved['image'], 'image'))
        if saved["audio"]:
            parts.append(plural(saved['audio'], 'audio file'))
        flash(f"Successfully uploaded {' and '.join(parts)}!", "success")
        return redirect(url_for("admin.generate_annotation_tasks", project_id=project.id))

    return render_template("admin/upload_images.html", project=project)


@admin.route("/generate-annotation-tasks/<int:project_id>", methods=["GET", "POST"])
@project_admin_required
def generate_annotation_tasks(project_id):
    """Generate annotation tasks and assign them to users"""
    project = db.get_or_404(Project, project_id)

    if not project.is_annotation_project:
        flash("This is not an annotation project!", "error")
        return redirect(url_for("admin.view_projects"))

    images = ProjectImage.query.filter_by(project_id=project_id).all()
    questions = Question.query.filter_by(project_id=project_id).all()
    groups = UserGroup.query.all()

    if request.method == "POST":
        selected_group_id = request.form.get("group_id")
        if not selected_group_id:
            flash("Please select a user group!", "error")
            return redirect(request.url)

        group = db.session.get(UserGroup, selected_group_id)
        if not group:
            flash("Selected group not found!", "error")
            return redirect(request.url)

        # Record that this group works on the project, so the project shows up
        # on the group's dashboards and in later redistribution.
        if not GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first():
            db.session.add(GroupProject(group_id=group.id, project_id=project.id, questions_distributed=True))
            db.session.commit()

        if not group.members:
            flash("The selected group has no members yet. Tasks were created and will be assigned when people join.", "warning")

        result = distribution.distribute(project)
        flash_distribution(result, f"Tasks are ready for '{project.name}'.")
        warn_idle_members(project, [m.user for m in group.members])
        return redirect(url_for("admin.view_annotation_project", project_id=project.id))

    assigned_group_ids = {gp.group_id for gp in GroupProject.query.filter_by(project_id=project.id)}
    return render_template("admin/generate_annotation_tasks.html",
                         project=project,
                         images=images,
                         questions=questions,
                         groups=groups,
                         assigned_group_ids=assigned_group_ids)


@admin.route("/view-annotation-project/<int:project_id>")
@project_admin_required
def view_annotation_project(project_id):
    """View annotation project details and progress"""
    project = db.get_or_404(Project, project_id)

    if not project.is_annotation_project:
        flash("This is not an annotation project!", "error")
        return redirect(url_for("admin.view_projects"))

    images = ProjectImage.query.filter_by(project_id=project_id).all()
    questions = Question.query.filter_by(project_id=project_id).all()
    tasks = AnnotationTask.query.filter_by(project_id=project_id).all()
    assignments = TaskAssignment.query.join(AnnotationTask).filter(AnnotationTask.project_id == project_id).all()
    
    # Calculate detailed statistics
    total_assignments = len(assignments)
    completed_assignments = len([a for a in assignments if a.is_completed])
    unique_users = {a.user_id for a in assignments}
    total_tasks = len(tasks)
    
    # Count truly completed tasks (those that have received required answers)
    completed_tasks = 0
    for task in tasks:
        # A task is complete once it has received the required number of answers
        task.is_completed = task.current_answers >= task.required_answers
        if task.is_completed:
            completed_tasks += 1
    db.session.commit()

    # Per-item completion statistics
    image_completion = {}
    for img in images:
        img_tasks = [t for t in tasks if t.image_id == img.id]
        total_img = len(img_tasks)
        completed_img = len([t for t in img_tasks if t.is_completed])
        image_completion[img.id] = {
            'total': total_img,
            'completed': completed_img,
            'percentage': round((completed_img / total_img * 100) if total_img else 0, 1)
        }

    # Calculate user assignment statistics
    user_assignments = {}
    for user_id in unique_users:
        user_assignments[user_id] = {
            'total': len([a for a in assignments if a.user_id == user_id]),
            'completed': len([a for a in assignments if a.user_id == user_id and a.is_completed])
        }
        user_assignments[user_id]['percentage'] = round(
            (user_assignments[user_id]['completed'] / user_assignments[user_id]['total'] * 100)
            if user_assignments[user_id]['total'] > 0 else 0, 1
        )

    stats = {
        'total_images': len(images),
        'total_audio': len([i for i in images if i.is_audio]),
        'total_questions': len(questions),
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'completion_percentage': round((completed_tasks / total_tasks * 100) if total_tasks > 0 else 0, 1),
        'total_assignments': total_assignments,
        'completed_assignments': completed_assignments,
        'assignment_completion': round((completed_assignments / total_assignments * 100) if total_assignments > 0 else 0, 1),
        'unique_users': len(unique_users),
        'avg_answers_per_task': round(total_assignments / total_tasks if total_tasks > 0 else 0, 1),
        'image_completion': image_completion,
        'questions_per_image': project.questions_per_image,
        'user_assignments': user_assignments
    }

    return render_template("admin/view_annotation_project.html",
                         project=project,
                         stats=stats,
                         workload=distribution.workload_summary(project),
                         exports=ExportLog.query.filter_by(project_id=project.id).order_by(ExportLog.id.desc()).limit(10).all(),
                         images=images[:10],  # Show first 10 images as preview
                         questions=questions)

@admin.route("/project/<int:project_id>/export.<file_format>")
@project_admin_required
def export_project(project_id, file_format):
    """Download every answer of an annotation project as CSV or JSON."""
    import json
    from flask import current_app
    from flask_login import current_user

    project = db.get_or_404(Project, project_id)
    if file_format not in ("csv", "json"):
        abort(404)
    if not project.is_annotation_project:
        flash("Dataset export is available for annotation projects. Use the text download for surveys.", "warning")
        return redirect(url_for("admin.view_projects"))

    data = dataset_export.build_project_export(project)
    db.session.add(ExportLog(project_id=project.id, user_id=current_user.id,
                             file_format=file_format, answer_count=data["totals"]["answers"]))
    db.session.commit()
    current_app.logger.info("EXPORT project=%s format=%s by user=%s", project.id, file_format, current_user.id)

    slug = secure_filename(project.name.lower().replace(" ", "-")) or f"project-{project.id}"
    if file_format == "csv":
        body, mimetype = dataset_export.to_csv(data).encode("utf-8"), "text/csv"
    else:
        body, mimetype = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"), "application/json"
    return send_file(io.BytesIO(body), as_attachment=True, download_name=f"{slug}.{file_format}", mimetype=mimetype)


# Project Admin Specific Routes
@admin.route("/my-projects")
@project_admin_access_required
def my_projects():
    user_id = session['id']
    # Get ProjectAdmin entries for the current user
    project_admin_entries = ProjectAdmin.query.filter_by(user_id=user_id).all()
    # Extract the Project objects from these entries
    projects = [entry.project for entry in project_admin_entries if entry.project]
    return render_template("admin/my_projects.html", projects=projects)

@admin.route("/project/<int:project_id>/dashboard")
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

@admin.route("/project/<int:project_id>/admins")
@project_admin_required
def project_admins(project_id):
    project = db.get_or_404(Project, project_id)
    project_admins = ProjectAdmin.query.filter_by(project_id=project_id).all()
    return render_template("admin/project_admins.html", project=project, project_admins=project_admins)

@admin.route("/project/<int:project_id>/admins/add", methods=['POST'])
@project_admin_required
def add_project_admin(project_id):
    project = db.get_or_404(Project, project_id)
    email = request.form.get('email')
    
    # Verify the current user is either a system admin or a project admin for this project
    current_user_id = session['id']
    current_user = db.session.get(User, current_user_id)
    
    # Only system admins or project admins for this project can add other project admins
    if not (current_user.role == 'admin' or is_project_admin(project_id)):
        flash('You do not have permission to add project admins.', 'danger')
        return redirect(url_for('admin.project_admins', project_id=project_id))
    
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.project_admins', project_id=project_id))
    
    # Check if a project admin already exists for this project
    existing_project_manager = ProjectAdmin.query.filter_by(project_id=project_id).first()
    if existing_project_manager:
        flash('This project already has a manager. Only one manager is allowed per project.', 'danger')
        return redirect(url_for('admin.project_admins', project_id=project_id))
    
    # Check if user is already a project admin for this project
    existing_admin = ProjectAdmin.query.filter_by(
        user_id=user.id,
        project_id=project_id
    ).first()
    
    if existing_admin:
        flash('User is already a project admin for this project.', 'warning')
        return redirect(url_for('admin.project_admins', project_id=project_id))
    
    # Create new project admin assignment
    project_admin = ProjectAdmin(
        user_id=user.id,
        project_id=project_id,
        assigned_by=current_user_id
    )
    
    # Ensure the user's role is 'project-admin' if they weren't already
    if user.role != 'project-admin' and user.role != 'admin':
        user.role = 'project-admin'
    
    db.session.add(project_admin)
    db.session.commit()
    
    flash('Project admin added successfully.', 'success')
    return redirect(url_for('admin.project_admins', project_id=project_id))

@admin.route("/project/<int:project_id>/admins/<int:admin_id>/remove", methods=['POST'])
@project_admin_required
def remove_project_admin(project_id, admin_id):
    project_admin = db.get_or_404(ProjectAdmin, admin_id)
    current_user_id = session['id']
    current_user = db.session.get(User, current_user_id)
    
    # Ensure the project admin being removed is for the current project
    if project_admin.project_id != project_id:
        flash('Invalid project admin.', 'danger')
        return redirect(url_for('admin.project_admins', project_id=project_id))
    
    # Check if current user has permission to remove this project admin
    # System admins can remove any project admin
    # Project admins can only remove project admins they assigned or other project admins (but not themselves)
    if current_user.role != 'admin':
        # Project admins can't remove themselves
        if project_admin.user_id == current_user_id:
            flash('You cannot remove yourself as a project admin.', 'danger')
            return redirect(url_for('admin.project_admins', project_id=project_id))
        
        # Project admins can only remove project admins they assigned or other project admins
        # (System admins can remove any project admin)
        if project_admin.assigned_by != current_user_id:
            # Check if the user being removed was assigned by a system admin
            assigner = db.session.get(User, project_admin.assigned_by)
            if assigner and assigner.role == 'admin':
                flash('You cannot remove a project admin assigned by a system admin.', 'danger')
                return redirect(url_for('admin.project_admins', project_id=project_id))
    
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
    return redirect(url_for('admin.project_admins', project_id=project_id))

@admin.route("/project/<int:project_id>/admins/create", methods=['GET', 'POST'])
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
        return redirect(url_for('admin.project_admins', project_id=project_id))
    
    if request.method == 'POST':
        fullname = request.form.get('fullname')
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Check if email already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('A user with this email already exists.', 'danger')
            return redirect(url_for('admin.create_project_admin', project_id=project_id))
        
        # Check if a project admin already exists for this project
        existing_project_manager = ProjectAdmin.query.filter_by(project_id=project_id).first()
        if existing_project_manager:
            flash('This project already has a manager. Only one manager is allowed per project.', 'danger')
            return redirect(url_for('admin.project_admins', project_id=project_id))

        # Create new user and assign as project admin
        new_user = User()
        new_user.register_project_admin(fullname, email, password, project_id, current_user_id)
        
        flash('New project admin created successfully.', 'success')
        return redirect(url_for('admin.project_admins', project_id=project_id))
    
    return render_template('admin/create_project_admin.html', project=project)

@admin.route("/project/<int:project_id>/groups")
@project_admin_required
def project_groups(project_id):
    project = db.get_or_404(Project, project_id)
    # In a real implementation, you'd fetch groups assigned to this project
    assigned_groups = [gp.group for gp in project.assigned_groups]
    return render_template("admin/project_groups.html", project=project, assigned_groups=assigned_groups)

@admin.route("/project/<int:project_id>/results")
@project_admin_required
def project_results(project_id):
    project = db.get_or_404(Project, project_id)
    # In a real implementation, you'd fetch and process results for this project
    return render_template("admin/project_results.html", project=project)

# New route for Project Admins to edit questions
@admin.route("/project/<int:project_id>/edit-questions", methods=['GET', 'POST'])
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
            return redirect(url_for('admin.project_dashboard', project_id=project_id))

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
            return redirect(url_for('admin.edit_project_questions', project_id=project.id))

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
            return redirect(url_for('admin.edit_project_questions', project_id=project.id))
        
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
            return redirect(url_for('admin.project_dashboard', project_id=project_id))

    return render_template("admin/edit_project_questions.html", project=project)