from flask import send_file, make_response
import csv
import io
import os
import uuid
import zipfile
import random
from functools import wraps

from flask import Blueprint, request, redirect, url_for, flash, render_template, session, abort
from sqlalchemy import delete
from werkzeug.utils import secure_filename

from .extns import db
from .models import Project, Question, User, AnswerType, QuestionAnswer, UserGroup, UserGroupMembership, GroupProject, UserQuestionAssignment, ProjectImage, AnnotationTask, TaskAssignment, ProjectAdmin
from .auth import is_project_admin

admin = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        if 'id' not in session:
            flash('Please log in to access admin features.', 'warning')
            return redirect(url_for('auth.login'))

        if session.get('role') != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('auth.login'))

        return func(*args, **kwargs)

    return decorated_function

def project_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('id'):
            flash('Please log in first.', 'warning')
            return redirect(url_for('auth.login'))
        
        project_id = kwargs.get('project_id')
        if session.get('role') == 'admin':
            # System admins have access to all projects
            return f(*args, **kwargs)

        # Check if user is a project admin for this specific project
        if not project_id or not is_project_admin(project_id):
            flash('You do not have permission to access this project.', 'danger')
            return redirect(url_for('admin.my_projects'))
        
        # Store the last accessed project ID in session for convenience
        session['last_project_id'] = project_id

        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

def project_admin_access_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('id'):
            flash('Please log in first.', 'warning')
            return redirect(url_for('auth.login'))
        
        # System admins always have access
        if session.get('role') == 'admin':
            return f(*args, **kwargs)
            
        # Check if user is a project admin for any project
        user_id = session.get('id')
        is_any_project_admin = ProjectAdmin.query.filter_by(user_id=user_id).first() is not None
        
        if not is_any_project_admin:
            flash('Project admin access required.', 'danger')
            return redirect(url_for('auth.login'))
            
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

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

    # Get group performance data
    group_stats = []
    groups = UserGroup.query.all()
    for group in groups:
        total_assigned_questions = UserQuestionAssignment.query.join(
            UserGroupMembership, UserQuestionAssignment.user_id == UserGroupMembership.user_id
        ).filter(UserGroupMembership.group_id == group.id).count()

        answered_questions = 0
        for member in group.members:
            answered_questions += QuestionAnswer.query.filter_by(user_id=member.user_id).count()

        completion_rate = (answered_questions / total_assigned_questions * 100) if total_assigned_questions > 0 else 0

        group_stats.append({
            'name': group.name,
            'members': len(group.members),
            'projects': len(group.assigned_projects),
            'completion_rate': round(completion_rate, 1)
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
    users = User.query.filter_by(role='end-user').all()
    return render_template('admin/users.html', users=users)

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
                
                # Generate a random password if not provided or too short
                if not password or len(password) < 6:
                    password = ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=10))
                    errors.append(f"Row {i+2}: No password provided for '{email}'. Generated temporary password.")

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
            group = UserGroup.query.get(selected_group_id)
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
            project = Project.query.get(selected_project_id)
            if project:
                project_assignments_made = 0
                if project.is_annotation_project:
                    all_tasks = AnnotationTask.query.filter_by(project_id=project.id).all()
                    if all_tasks:
                        # Use assign_tasks_to_users for annotation projects
                        assignments_created = assign_tasks_to_users(all_tasks, users_to_assign, project.max_questions_per_user, project.answers_per_task)
                        if assignments_created > 0:
                            flash(f"Assigned {assignments_created} annotation tasks to users for project '{project.name}'.", 'success')
                        else:
                            flash(f"No new annotation tasks assigned to users for project '{project.name}' (they might already be assigned or no tasks available).", 'info')
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
           
                                project_assignments_made += 1
                    db.session.commit()
                    if project_assignments_made > 0:
                        flash(f"{project_assignments_made} questions assigned to users for project '{project.name}'.", 'success')
                    else:
                        flash(f"All users from CSV are already assigned questions in project '{project.name}'.", 'info')
            else:
                flash("Selected project not found for assignment.", 'warning')

        summary_message = f"CSV Import Complete: {users_created} users created, {users_skipped_duplicate} duplicates skipped, {users_skipped_invalid} invalid rows skipped."
        flash(summary_message, 'info')
        for error in errors:
            flash(error, 'warning')

        return redirect(url_for('admin.users')) # Redirect to users list

    groups = UserGroup.query.all()
    projects = Project.query.all()
    return render_template('admin/import_users.html', groups=groups, projects=projects)

@admin.route("/create-project", methods=['GET', 'POST'])
@admin_required
def create_project():

    if request.method == 'POST':
        project_name = request.form.get("project_name")
        project_desc = request.form.get("project_desc")

        project = Project(name=project_name,
                          desc=project_desc)
        db.session.add(project)
        db.session.commit()

        # Save questions
        total_questions = int(request.form.get("total_questions"))
        for i in range(1, total_questions + 1):
            file_key = f'question-{i}-file'
            question_text = request.form.get(f"question-{i}")
            file_path = request.files.get(f"question-{i}-file")
            filename = None
            if file_path:
                filename = str(uuid.uuid4()) + \
                    secure_filename(file_path.filename)

                file_path.save(os.path.join(
                    'application', 'static', 'uploads', filename))

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

        db.session.commit()

        flash("Form data saved successfully!", "success")
        return redirect(url_for("admin.view_projects"))
    return render_template("admin/create_project.html")


@admin.route("/view-projects")
@admin_required
def view_projects():
    projects = Project.query.all()

    return render_template("admin/view_projects.html", projects=projects)


@admin.route("/delete-project/<int:project_id>")
@admin_required
def delete_project(project_id):
    project = Project.query.get_or_404(project_id)

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
@admin_required
def edit_project(project_id):
    project = Project.query.get_or_404(project_id)

    if request.method == 'POST':
        project_data = request.form.to_dict(flat=True)
        project_name = project_data.get('project-name')
        project_desc = project_data.get('project-description')

        project.name = project_name
        project.desc = project_desc

        files = request.files.to_dict(flat=True)

        for key, file in files.items():
            if key.startswith('questionfile-'):
                question_id = int(key.split('-')[1])
                question = Question.query.get(question_id)
                if question:
                    file = request.files.get(key)
                    filename = None
                    if file:
                        filename = str(uuid.uuid4()) + \
                            secure_filename(file.filename)
                        file_path = os.path.join('application', 'static', 'uploads', filename)
                        file.save(file_path)

                    if file.filename:
                        question.file_path = filename

        # Update annotation project settings if applicable
        if project.is_annotation_project:
            project.max_questions_per_user = int(request.form.get('max_questions_per_user', project.max_questions_per_user))
            project.answers_per_task = int(request.form.get('answers_per_task', project.answers_per_task))

        db.session.commit()
        flash('Project details updated successfully!', 'success')
        return redirect(url_for("admin.view_projects"))

    return render_template("admin/edit_project.html", project=project)


@admin.route("/view-questions/<int:project_id>")
@project_admin_required
def view_questions(project_id):
    project = Project.query.get_or_404(project_id)
    # This route is now for both System Admins and Project Admins to view questions
    return render_template("admin/view_questions.html", project=project)


# Individual project assignment removed - only group assignments supported


@admin.route("/download-files/<int:project_id>")
def download_files(project_id):
    project = Project.query.get(project_id)
    if project:
        output = io.StringIO()
        output.write(f"Project Name: {project.name}\n")
        output.write(f"Project Description: {project.desc}\n\n")

        for question in project.questions:
            output.write(f"  Question: {question.question}\n")
            file_path = "No File"
            if question.file_path:
                file_path = f"http://localhost:5000/static/uploads/{question.file_path}"
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


# Download answers per user (group assignments only)
@admin.route("/download-answers/<int:user_id>")
def download_answers(user_id):
    user = User.query.get(user_id)
    if user:
        output = io.StringIO()
        writer = csv.writer(output)

        # Get user's question assignments from groups
        assignments = UserQuestionAssignment.query.filter_by(user_id=user_id).all()

        if assignments:
            # Group assignments by project
            projects_data = {}
            for assignment in assignments:
                question = assignment.question
                project = question.belongs_to

                if project.id not in projects_data:
                    projects_data[project.id] = {
                        'project': project,
                        'questions': []
                    }
                projects_data[project.id]['questions'].append(question)

            # Write data for each project
            for project_data in projects_data.values():
                project = project_data['project']
                questions = project_data['questions']

                writer.writerow(['Project Name', project.name])
                writer.writerow(['Project Description', project.desc])
                writer.writerow(['Question', 'Answer', 'File Path'])

                for question in questions:
                    answer_obj = QuestionAnswer.query.filter_by(user_id=user_id, question_id=question.id).first()
                    answer = answer_obj.answer if answer_obj else 'Not answered'
                    file_path = 'No File'
                    if question.file_path:
                        file_path = f"http://localhost:5000/static/uploads/{question.file_path}"
                    writer.writerow([question.question, answer, file_path])
                writer.writerow([''])  # Empty line
        else:
            writer.writerow(['No assignments found for this user'])

        output.seek(0)

        # Convert StringIO to BytesIO for send_file
        mem = io.BytesIO()
        mem.write(output.getvalue().encode())
        mem.seek(0)

        response = make_response(send_file(
            mem, as_attachment=True, download_name='user_answers.txt', mimetype='text/plain'))
        return response
    else:
        flash('User not found', 'danger')
        return redirect(url_for('admin.view_groups'))


@admin.route("/profile", methods=['GET', 'POST'])
@admin_required
def admin_profile():
    admin_user = User.query.get(session['id'])

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
                file_path = f"http://localhost:5000/static/uploads/{question.file_path}"
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


# Group Management Routes
@admin.route("/groups")
@admin_required
def view_groups():
    groups = UserGroup.query.all()
    return render_template('admin/groups.html', groups=groups)


@admin.route("/create-group", methods=['GET', 'POST'])
@admin_required
def create_group():
    if request.method == 'POST':
        group_name = request.form.get("group_name")
        group_desc = request.form.get("group_desc")

        # Check if group name already exists
        existing_group = UserGroup.query.filter_by(name=group_name).first()
        if existing_group:
            flash('Group name already exists!', 'danger')
            return redirect(url_for("admin.create_group"))

        group = UserGroup(name=group_name, description=group_desc)
        db.session.add(group)
        db.session.commit()

        flash("Group created successfully!", "success")
        return redirect(url_for("admin.view_groups"))

    return render_template("admin/create_group.html")


@admin.route("/manage-group/<int:group_id>", methods=['GET', 'POST'])
@admin_required
def manage_group(group_id):
    group = UserGroup.query.get_or_404(group_id)

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_user':
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)

            if user:
                # Check if user is already in group
                existing = UserGroupMembership.query.filter_by(user_id=user.id, group_id=group.id).first()
                if existing:
                    flash('User is already in this group', 'warning')
                else:
                    membership = UserGroupMembership(user_id=user.id, group_id=group.id)
                    db.session.add(membership)
                    db.session.commit()
                    assign_questions_to_new_group_member(user, group) # Call new function
                    flash('User added to group successfully and assignments updated', 'success')
            else:
                flash('User not found', 'danger')

        elif action == 'remove_user':
            user_id = request.form.get('user_id')
            user = User.query.get(user_id) # Get the user object
            membership = UserGroupMembership.query.filter_by(user_id=user_id, group_id=group.id).first()
            if membership and user:
                remove_pending_assignments_from_removed_group_member(user, group) # Call new function
                db.session.delete(membership)
                db.session.commit()
                flash('User removed from group successfully and pending assignments removed', 'success')
            else:
                flash('User not found in group or user object not found', 'danger')

        return redirect(url_for("admin.manage_group", group_id=group_id))

    # Get all users not in this group
    current_member_ids = [m.user_id for m in group.members]
    available_users = User.query.filter(
        User.role == 'end-user',
        ~User.id.in_(current_member_ids)
    ).all()

    return render_template("admin/manage_group.html", group=group, available_users=available_users)


def assign_questions_to_new_group_member(user, group):
    """
    Assigns all active questions/tasks from projects assigned to the group
    to the newly added user.
    """
    assigned_projects = GroupProject.query.filter_by(group_id=group.id).all()
    for gp in assigned_projects:
        project = gp.project
        group_project_id_str = f"{group.id}-{project.id}"

        if not project.is_annotation_project:
            # Regular project: assign all questions
            for question in project.questions:
                existing_assignment = UserQuestionAssignment.query.filter_by(
                    user_id=user.id,
                    question_id=question.id,
                    group_project_id=group_project_id_str
                ).first()
                if not existing_assignment:
                    assignment = UserQuestionAssignment(
                        user_id=user.id,
                        question_id=question.id,
                        group_project_id=group_project_id_str
                    )
                    db.session.add(assignment)
        else:
            # Annotation project: assign tasks
            all_tasks = AnnotationTask.query.filter_by(project_id=project.id).all()
            # The assign_tasks_to_users function already handles existing assignments and max_questions_per_user
            # We need to pass a list containing only the new user
            assign_tasks_to_users(all_tasks, [user], project.max_questions_per_user, project.answers_per_task)
    db.session.commit()


def remove_pending_assignments_from_removed_group_member(user, group):
    """
    Removes pending (unanswered/incomplete) assignments for a user
    from projects associated with the group they are being removed from.
    Completed answers are kept.
    """
    assigned_projects = GroupProject.query.filter_by(group_id=group.id).all()
    for gp in assigned_projects:
        project = gp.project
        group_project_id_str = f"{group.id}-{project.id}"

        if not project.is_annotation_project:
            # Regular project: remove pending UserQuestionAssignment
            pending_assignments = UserQuestionAssignment.query.filter_by(
                user_id=user.id,
                group_project_id=group_project_id_str
            ).all()
            for assignment in pending_assignments:
                # Check if there's an answer for this assignment
                existing_answer = QuestionAnswer.query.filter_by(
                    user_id=user.id,
                    question_id=assignment.question_id
                ).first()
                if not existing_answer: # Only delete if no answer exists
                    db.session.delete(assignment)
        else:
            # Annotation project: remove pending TaskAssignment
            # Find tasks associated with this project
            project_tasks = AnnotationTask.query.filter_by(project_id=project.id).all()
            project_task_ids = [task.id for task in project_tasks]

            pending_task_assignments = TaskAssignment.query.filter(
                TaskAssignment.user_id == user.id,
                TaskAssignment.task_id.in_(project_task_ids),
                TaskAssignment.is_completed == False # Only remove pending ones
            ).all()
            for task_assignment in pending_task_assignments:
                db.session.delete(task_assignment)
    db.session.commit()


@admin.route("/assign-project-to-group", methods=['GET', 'POST'])
@admin_required
def assign_project_to_group():
    if request.method == 'POST':
        group_id = request.form.get('group_id')
        project_id = request.form.get('project_id')
        group = UserGroup.query.get(group_id)
        project = Project.query.get(project_id)

        if group and project:
            # Check if project is already assigned to group
            existing = GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first()
            if existing:
                flash('This project is already assigned to this group', 'warning')
                return redirect(url_for("admin.assign_project_to_group"))

            # Create group project assignment
            group_project = GroupProject(group_id=group.id, project_id=project.id)
            db.session.add(group_project)
            db.session.commit()

            # Distribute questions among group members
            distribute_questions_to_group(group, project)

            flash('Project assigned to group and questions distributed successfully', 'success')
            return redirect(url_for("admin.assign_project_to_group"))
        else:
            flash('Invalid selection', 'danger')
            return redirect(url_for("admin.assign_project_to_group"))

    groups = UserGroup.query.all()
    projects = Project.query.all()
    return render_template("admin/assign_project_to_group.html", groups=groups, projects=projects)


def distribute_questions_to_group(group, project):
    """Distribute questions evenly among group members"""
    members = [m.user for m in group.members]
    questions = project.questions

    if not members or not questions:
        return

    # Create a unique identifier for this group-project combination
    group_project_id = f"{group.id}-{project.id}"

    # Distribute questions evenly
    questions_per_user = len(questions) // len(members)
    remaining_questions = len(questions) % len(members)

    question_index = 0
    for i, member in enumerate(members):
        # Calculate how many questions this user gets
        user_question_count = questions_per_user
        if i < remaining_questions:
            user_question_count += 1

        # Assign questions to this user
        for j in range(user_question_count):
            if question_index < len(questions):
                assignment = UserQuestionAssignment(
                    user_id=member.id,
                    question_id=questions[question_index].id,
                    group_project_id=group_project_id
                )
                db.session.add(assignment)
                question_index += 1

    # Mark questions as distributed
    group_project = GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first()
    if group_project:
        group_project.questions_distributed = True

    db.session.commit()


@admin.route("/delete-group/<int:group_id>")
@admin_required
def delete_group(group_id):
    group = UserGroup.query.get_or_404(group_id)

    # Delete all related records
    # Delete question assignments for this group's projects
    for group_project in group.assigned_projects:
        group_project_id = f"{group.id}-{group_project.project_id}"
        assignments = UserQuestionAssignment.query.filter_by(group_project_id=group_project_id).all()
        for assignment in assignments:
            db.session.delete(assignment)

    # Delete group project assignments
    for group_project in group.assigned_projects:
        db.session.delete(group_project)

    # Delete group memberships
    for membership in group.members:
        db.session.delete(membership)

    # Delete the group itself
    db.session.delete(group)
    db.session.commit()

    flash("Group deleted successfully!", "info")
    return redirect(url_for("admin.view_groups"))


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
        answers_per_task = int(request.form.get("answers_per_task", 1)) # Default to 1 for single completion
        total_questions = int(request.form.get("total_questions", 0))
        assigned_group_id = request.form.get("assigned_group_id")

        # Validate total_questions
        if total_questions <= 0:
            flash("Please specify a valid number of questions!", "error")
            return redirect(request.url)

        # Create the annotation project
        project = Project(
            name=project_name,
            desc=project_desc,
            user_id=session["id"],
            is_annotation_project=True,
            max_questions_per_user=max_questions_per_user,
            answers_per_task=answers_per_task,
            questions_per_image=total_questions  # Use total_questions directly
        )
        db.session.add(project)
        db.session.flush()  # Get the project ID

        # Assign project to selected group
        if assigned_group_id:
            group = UserGroup.query.get(assigned_group_id)
            if group:
                # Check if project is already assigned to this group
                existing_assignment = GroupProject.query.filter_by(
                    group_id=group.id,
                    project_id=project.id
                ).first()
                
                if not existing_assignment:
                    # Create new group-project assignment
                    group_project = GroupProject(
                        group_id=group.id,
                        project_id=project.id
                    )
                    db.session.add(group_project)
                    db.session.commit()
                    
                    # Distribute questions to group members
                    distribute_questions_to_group(group, project)

        # Create the custom questions from form input
        custom_questions = []
        for i in range(1, total_questions + 1):
            question_text = request.form.get(f"question_{i}")
            if question_text:
                custom_questions.append(question_text.strip())

        # Ensure we have exactly the specified number of questions
        if len(custom_questions) != total_questions:
            flash(f"Please provide exactly {total_questions} questions!", "error")
            return redirect(request.url)

        # Create the questions in database
        for i, question_text in enumerate(custom_questions, 1):
            question = Question(
                question=question_text,
                answer_type=AnswerType.TEXT,
                project_id=project.id
            )
            db.session.add(question)

        db.session.commit()
        flash(f"Annotation project '{project_name}' created with {total_questions} questions!", "success")
        return redirect(url_for("admin.upload_images", project_id=project.id))

    groups = UserGroup.query.all()
    return render_template("admin/create_annotation_project.html", groups=groups)


@admin.route("/upload-images/<int:project_id>", methods=["GET", "POST"])
@project_admin_required
def upload_images(project_id):
    """Upload images for annotation project"""
    project = Project.query.get_or_404(project_id)

    if not project.is_annotation_project:
        flash("This is not an annotation project!", "error")
        return redirect(url_for("admin.view_projects"))

    if request.method == "POST":
        if 'image_zip' not in request.files:
            flash("No ZIP file uploaded!", "error")
            return redirect(request.url)

        zip_file = request.files['image_zip']
        if zip_file.filename == '':
            flash("No file selected!", "error")
            return redirect(request.url)

        if zip_file and zip_file.filename.lower().endswith(('.zip')):
            # Create upload directory
            upload_dir = os.path.join('application', 'static', 'uploads', 'images', str(project_id))
            os.makedirs(upload_dir, exist_ok=True)

            # Extract and save images
            images_saved = 0
            with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                for file_info in zip_ref.filelist:
                    if not file_info.is_dir() and file_info.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                        # Extract file
                        zip_ref.extract(file_info, upload_dir)

                        # Generate unique filename
                        original_filename = os.path.basename(file_info.filename)
                        unique_filename = f"{uuid.uuid4()}_{secure_filename(original_filename)}"

                        # Move to final location
                        extracted_path = os.path.join(upload_dir, file_info.filename)
                        final_path = os.path.join(upload_dir, unique_filename)
                        os.rename(extracted_path, final_path)

                        # Save to database
                        project_image = ProjectImage(
                            project_id=project_id,
                            filename=unique_filename,
                            original_filename=original_filename,
                            file_path=final_path
                        )
                        db.session.add(project_image)
                        images_saved += 1

            db.session.commit()
            flash(f"Successfully uploaded {images_saved} images!", "success")
            return redirect(url_for("admin.generate_annotation_tasks", project_id=project.id))
        else:
            flash("Please upload a ZIP file!", "error")

    return render_template("admin/upload_images.html", project=project)


def _generate_project_tasks(project_id):
    """Helper function to generate all annotation tasks (image x question combinations) for a project."""
    project = Project.query.get_or_404(project_id)
    images = ProjectImage.query.filter_by(project_id=project_id).all()
    questions = Question.query.filter_by(project_id=project_id).all()

    # Clear existing tasks and assignments for this project before regenerating
    existing_tasks = AnnotationTask.query.filter_by(project_id=project_id).all()
    for task in existing_tasks:
        TaskAssignment.query.filter_by(task_id=task.id).delete() # Delete task assignments first
        db.session.delete(task)
    db.session.commit() # Commit deletions before adding new tasks

    tasks_created = 0
    new_tasks = []
    if images and questions:
        for image in images:
            for question in questions:
                task = AnnotationTask(
                    project_id=project_id,
                    image_id=image.id,
                    question_id=question.id,
                    required_answers=project.answers_per_task,
                    current_answers=0,
                    is_completed=False
                )
                db.session.add(task)
                new_tasks.append(task) # Collect new tasks to return
                tasks_created += 1
    db.session.commit() # Commit new tasks

    print(f"Generated {tasks_created} new annotation tasks for project {project_id}.")
    return new_tasks, tasks_created


@admin.route("/generate-annotation-tasks/<int:project_id>", methods=["GET", "POST"])
@project_admin_required
def generate_annotation_tasks(project_id):
    """Generate annotation tasks and assign them to users"""
    project = Project.query.get_or_404(project_id)

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

        # Use the helper function to generate tasks
        new_tasks, tasks_created = _generate_project_tasks(project_id)

        group = UserGroup.query.get(selected_group_id)
        if not group:
            flash("Selected group not found!", "error")
            return redirect(request.url)

        # Assign these newly generated tasks to users in the selected group
        users_in_group = [m.user for m in group.members if m.user]
        if not users_in_group:
            flash("No active users in the selected group to assign tasks to.", "warning")
            # We still redirect to project dashboard even if no assignments are made
            return redirect(url_for("admin.view_annotation_project", project_id=project.id))

        assignments_created = assign_tasks_to_users(new_tasks, users_in_group, project.max_questions_per_user, project.answers_per_task)
        
        flash(f"Created {tasks_created} annotation tasks and {assignments_created} user assignments!", "success")
        return redirect(url_for("admin.view_annotation_project", project_id=project.id))

    return render_template("admin/generate_annotation_tasks.html",
                         project=project,
                         images=images,
                         questions=questions,
                         groups=groups)


def assign_tasks_to_users(tasks, users, max_questions_per_user, answers_per_task):
    """
    Efficient algorithm to assign annotation tasks to users.
    Prioritizes incomplete tasks and distributes them in a round-robin fashion
    to maximize completion and information gain.
    """
    assignments_created = 0
    user_task_counts = {user.id: 0 for user in users}
    
    if not users:
        print("No users available for assignment.")
        return 0

    try:
        from itertools import cycle
        import random
        tasks_sorted = list(tasks)
        random.shuffle(tasks_sorted)
        
        user_cycle = cycle(users)
        
        print(f"   - Total tasks to assign: {len(tasks_sorted)} | Users: {len(users)} | Max/user: {max_questions_per_user} | Answers/Task: {answers_per_task}")
        
        for task in tasks_sorted:
            # Skip if task already fully answered (should be 0 after clearing tasks)
            if task.current_answers >= task.required_answers:
                continue

            assigned_for_this_task = 0
            users_assigned_this_task_current_run = set() # Track users assigned *this specific task* in this run

            # Get existing assignments for this task, if any, to avoid re-assigning
            existing_assignments_for_task = TaskAssignment.query.filter_by(task_id=task.id).all()
            for existing_assign in existing_assignments_for_task:
                users_assigned_this_task_current_run.add(existing_assign.user_id)
                assigned_for_this_task += 1
            
            # If we've already met the required answers for this task, skip
            if assigned_for_this_task >= task.required_answers:
                continue

            attempts_to_find_user_for_this_task = 0
            # Try to assign this task to 'required_answers' distinct users
            # The loop continues until 'required_answers' are met for THIS task, or we've tried all users a couple of times
            while assigned_for_this_task < task.required_answers and attempts_to_find_user_for_this_task < len(users) * 2:
                current_user = next(user_cycle)
                attempts_to_find_user_for_this_task += 1

                # If user already assigned this task (in this run or previously committed)
                if current_user.id in users_assigned_this_task_current_run:
                    continue

                # If user has not reached their max task limit
                if user_task_counts[current_user.id] < max_questions_per_user:
                    assignment = TaskAssignment(
                        task_id=task.id,
                        user_id=current_user.id
                    )
                    db.session.add(assignment)
                    user_task_counts[current_user.id] += 1
                    assignments_created += 1
                    assigned_for_this_task += 1
                    users_assigned_this_task_current_run.add(current_user.id) # Mark user as having this task
                
            if assigned_for_this_task < task.required_answers:
                print(f"⚠️  Task {task.id} (Q: {task.question.question[:20]}) could not be assigned to {task.required_answers} different users. Assigned to {assigned_for_this_task} users.")
            
        db.session.commit() # Commit all assignments at once

        print(f"✅ Assignment completed:")
        print(f"   - Total assignments created: {assignments_created}")
        print(f"   - Average tasks per user: {assignments_created / len(users) if users else 0:.1f}")

        return assignments_created

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error during task assignment: {str(e)}")
        return 0


@admin.route("/view-annotation-project/<int:project_id>")
@project_admin_required
def view_annotation_project(project_id):
    """View annotation project details and progress"""
    project = Project.query.get_or_404(project_id)

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
        # Check if task is truly completed based on answers
        if task.current_answers >= task.required_answers:
            completed_tasks += 1
            task.is_completed = True  # Update the is_completed flag
        else:
            task.is_completed = False
        
        # Update tasks state and commit once
        db.session.commit()

        # Build per-image completion statistics
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
                         images=images[:10],  # Show first 10 images as preview
                         questions=questions)

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
    project = Project.query.get_or_404(project_id)
    
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
    project = Project.query.get_or_404(project_id)
    project_admins = ProjectAdmin.query.filter_by(project_id=project_id).all()
    return render_template("admin/project_admins.html", project=project, project_admins=project_admins)

@admin.route("/project/<int:project_id>/admins/add", methods=['POST'])
@project_admin_required
def add_project_admin(project_id):
    project = Project.query.get_or_404(project_id)
    email = request.form.get('email')
    
    # Verify the current user is either a system admin or a project admin for this project
    current_user_id = session['id']
    current_user = User.query.get(current_user_id)
    
    # Only system admins or project admins for this project can add other project admins
    if not (current_user.role == 'admin' or is_project_admin(project_id)):
        flash('You do not have permission to add project admins.', 'danger')
        return redirect(url_for('admin.project_admins', project_id=project_id))
    
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('User not found.', 'danger')
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
    project_admin = ProjectAdmin.query.get_or_404(admin_id)
    current_user_id = session['id']
    current_user = User.query.get(current_user_id)
    
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
            assigner = User.query.get(project_admin.assigned_by)
            if assigner and assigner.role == 'admin':
                flash('You cannot remove a project admin assigned by a system admin.', 'danger')
                return redirect(url_for('admin.project_admins', project_id=project_id))
    
    # Get the user whose project admin role is being removed
    user = User.query.get(project_admin.user_id)
    
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
    project = Project.query.get_or_404(project_id)
    
    # Verify the current user is either a system admin or a project admin for this project
    current_user_id = session['id']
    current_user = User.query.get(current_user_id)
    
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
        
        # Create new user and assign as project admin
        new_user = User()
        new_user.register_project_admin(fullname, email, password, project_id, current_user_id)
        
        flash('New project admin created successfully.', 'success')
        return redirect(url_for('admin.project_admins', project_id=project_id))
    
    return render_template('admin/create_project_admin.html', project=project)

@admin.route("/project/<int:project_id>/groups")
@project_admin_required
def project_groups(project_id):
    project = Project.query.get_or_404(project_id)
    # In a real implementation, you'd fetch groups assigned to this project
    assigned_groups = [gp.group for gp in project.assigned_groups]
    return render_template("admin/project_groups.html", project=project, assigned_groups=assigned_groups)

@admin.route("/project/<int:project_id>/results")
@project_admin_required
def project_results(project_id):
    project = Project.query.get_or_404(project_id)
    # In a real implementation, you'd fetch and process results for this project
    return render_template("admin/project_results.html", project=project)

# New route for Project Admins to edit questions
@admin.route("/project/<int:project_id>/edit-questions", methods=['GET', 'POST'])
@project_admin_required
def edit_project_questions(project_id):
    project = Project.query.get_or_404(project_id)

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_questions':
            project_data = request.form.to_dict(flat=True)
            files = request.files.to_dict(flat=True)

            # Update annotation project settings if applicable
            if project.is_annotation_project:
                project.max_questions_per_user = int(request.form.get('max_questions_per_user', project.max_questions_per_user))
                project.answers_per_task = int(request.form.get('answers_per_task', 1)) # Default to 1 for single completion
                project.questions_per_image = int(request.form.get('questions_per_image', project.questions_per_image))

            for key, value in project_data.items():
                if key.startswith('question-'):
                    question_id = int(key.split('-')[1])
                    question = Question.query.get(question_id)
                    if question:
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
                                    confirm_delete = request.form.get(f'confirm_delete_answers_{question_id}')
                                    if confirm_delete == 'true':
                                        # Delete existing answers
                                        QuestionAnswer.query.filter_by(question_id=question_id).delete()
                                        flash(f'Existing answers for question "{question.question}" were deleted due to type change.', 'info')
                                    else:
                                        flash(f'Cannot change question type for "{question.question}" as it has existing answers. Check "Delete existing answers" to proceed.', 'danger')
                                        # Skip updating answer type and options for this question
                                    continue

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
                             upload_dir = os.path.join('application', 'static', 'uploads')
                             os.makedirs(upload_dir, exist_ok=True)
                             file_path = os.path.join(upload_dir, filename)
                             file.save(file_path)
                             question.file_path = filename


            db.session.commit()
            flash('Project questions and settings updated successfully!', 'success')
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
            
            # After adding a question, regenerate all tasks and re-assign
            if project.is_annotation_project:
                new_tasks, tasks_created = _generate_project_tasks(project.id)
                # Attempt to assign to all groups associated with the project
                for group_project in project.assigned_groups:
                    group = group_project.group
                    if group:
                        users_in_group = [m.user for m in group.members if m.user]
                        if users_in_group:
                            assign_tasks_to_users(new_tasks, users_in_group, project.max_questions_per_user, project.answers_per_task)
                            flash(f"Tasks regenerated and re-assigned for group '{group.name}'.", "info")
            
            # Redirect back to the edit page to see the new question
            return redirect(url_for('admin.edit_project_questions', project_id=project.id))

        elif action == 'delete_question':
            question_id_to_delete = request.form.get('question_id_to_delete')
            question = Question.query.get(question_id_to_delete)
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
                
                db.session.delete(question)
                db.session.commit()
                flash('Question deleted.', 'success')
                
                # After deleting a question, regenerate all tasks and re-assign
                if project.is_annotation_project:
                    new_tasks, tasks_created = _generate_project_tasks(project.id)
                    # Attempt to assign to all groups associated with the project
                    for group_project in project.assigned_groups:
                        group = group_project.group
                        if group:
                            users_in_group = [m.user for m in group.members if m.user]
                            if users_in_group:
                                assign_tasks_to_users(new_tasks, users_in_group, project.max_questions_per_user, project.answers_per_task)
                                flash(f"Tasks regenerated and re-assigned for group '{group.name}'.", "info")
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
                db.session.commit()
                flash('Project settings updated successfully!', 'success')
            return redirect(url_for('admin.project_dashboard', project_id=project_id))

    return render_template("admin/edit_project_questions.html", project=project)