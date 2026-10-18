from flask import send_file, make_response
import csv
import io
import os
import uuid
import secrets

from flask import Blueprint, request, redirect, url_for, flash, render_template, session
from sqlalchemy import delete
from werkzeug.utils import secure_filename

from .extns import db
from .models import Project, Question, User, AnswerType, QuestionAnswer, UserGroup, UserGroupMembership, GroupProject, UserQuestionAssignment, AnnotationTask, TaskAssignment, ExportLog
from .auth import MIN_PASSWORD_LENGTH
from .jinja_utils import plural
from . import distribution
from .feedback import flash_distribution, warn_idle_members
from .groups import assign_questions_to_new_group_member, group_progress, project_progress, set_up_new_project
from .permissions import admin_required, project_admin_access_required, project_admin_required

admin = Blueprint("admin", __name__, url_prefix="/admin")

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
            'week': f"{week_start.day} {week_start:%b}",  # the week's first day, e.g. "3 Aug"
            'answers': answers_count
        })

    # Reverse to show oldest to newest
    weeks_data.reverse()

    # Group progress: the share of this group's work that is done, counting
    # annotation tasks (finished assignments) and survey questions (answered).
    group_stats = []
    for group in UserGroup.query.all():
        project_ids = [gp.project_id for gp in group.assigned_projects]
        done, total = group_progress(group, project_ids)
        group_stats.append({
            'name': group.name,
            'members': len(group.members),
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
            'answer_count': QuestionAnswer.query.filter_by(user_id=user.id).count(),
            'assigned_groups': [m.group for m in user_group_memberships if m.group]
        })
    return render_template('admin/users.html', users_data=users_data)

@admin.route("/create-user", methods=['GET', 'POST'])
@admin_required
def create_user():
    """Add one person by hand: an annotator (optionally straight into a group) or a manager."""
    groups = UserGroup.query.order_by(UserGroup.name).all()
    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        if not full_name or not email:
            flash('Please enter a name and an email address.', 'danger')
            return render_template('admin/create_user.html', groups=groups, form=request.form, tab='one')
        if User.query.filter_by(email=email).first():
            flash(f'A user with the email {email} already exists.', 'danger')
            return render_template('admin/create_user.html', groups=groups, form=request.form, tab='one')
        if password and len(password) < MIN_PASSWORD_LENGTH:
            flash(f'The password must be at least {MIN_PASSWORD_LENGTH} characters long.', 'danger')
            return render_template('admin/create_user.html', groups=groups, form=request.form, tab='one')

        temporary = not password
        if temporary:
            password = secrets.token_urlsafe(8)
        as_manager = request.form.get('role') == 'manager'
        user = User()
        user.register_end_user(full_name, email, password)
        if as_manager:
            user.role = 'project-admin'  # no project yet; one can be given on the Managers page
        db.session.commit()
        if temporary:
            flash(f'{email} was given the temporary password {password}. '
                  'Share it with them and ask them to change it after signing in.', 'warning')

        if as_manager:
            flash(f'{full_name} was created as a manager. Give them a project on the Managers page.', 'success')
            return redirect(url_for('manager.managers'))

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

    return render_template('admin/create_user.html', groups=groups, projects=Project.query.all(),
                           form={'role': request.args.get('role', 'annotator')}, tab=request.args.get('tab'))


@admin.route("/import-users", methods=['GET', 'POST'])
@admin_required
def import_users():
    """Create people from a CSV file, then optionally add them to a group and/or a project."""
    if request.method == 'GET':
        return redirect(url_for('admin.create_user', tab='csv'))  # the form lives on the Add users page

    csv_file = request.files.get('csv_file')
    if csv_file is None:
        flash('No file part', 'danger')
        return redirect(url_for('admin.create_user', tab='csv'))
    if csv_file.filename == '':
        flash('No selected file', 'danger')
        return redirect(url_for('admin.create_user', tab='csv'))
    if not csv_file.filename.lower().endswith('.csv'):
        flash('Invalid file type. Please upload a CSV file.', 'danger')
        return redirect(url_for('admin.create_user', tab='csv'))

    rows = list(csv.reader(io.StringIO(csv_file.stream.read().decode("UTF8"))))
    header = rows[0] if rows else None
    if not header or not all(col in header for col in ['full_name', 'email', 'password']):
        flash('CSV must contain "full_name", "email", and "password" columns.', 'danger')
        return redirect(url_for('admin.create_user', tab='csv'))

    users, created, duplicates, invalid, messages = _create_users_from_rows(header, rows[1:])
    if request.form.get('user_group_id'):
        _add_imported_to_group(users, request.form['user_group_id'])
    if request.form.get('project_id'):
        _give_imported_project(users, request.form['project_id'])

    flash(f"CSV Import Complete: {plural(created, 'user')} created, "
          f"{plural(duplicates, 'duplicate')} skipped, "
          f"{plural(invalid, 'invalid row')} skipped.", 'info')
    for message in messages:
        flash(message, 'warning')
    return redirect(url_for('admin.users'))


def _create_users_from_rows(header, rows):
    """Create a member per row. Returns (people to place, created, duplicates, invalid, messages).

    People who already exist are not created again but are still placed in the group/project.
    """
    name_col, email_col, password_col = (header.index(c) for c in ('full_name', 'email', 'password'))
    users, messages = [], []
    created = duplicates = invalid = 0
    for line, row in enumerate(rows, start=2):  # line 1 is the header
        try:
            full_name = row[name_col].strip()
            email = row[email_col].strip()
            password = row[password_col].strip() if password_col < len(row) else ''

            if not full_name or not email:
                invalid += 1
                messages.append(f"Row {line}: Missing full name or email. Skipped.")
                continue

            existing = User.query.filter_by(email=email).first()
            if existing:
                duplicates += 1
                messages.append(f"Row {line}: User with email '{email}' already exists. Skipped creation, but will attempt assignment.")
                users.append(existing)
                continue

            # Missing or too-short passwords are replaced with a random one.
            # The admin must see it, otherwise the user could never sign in.
            if not password or len(password) < MIN_PASSWORD_LENGTH:
                reason = "had no password" if not password else f"had a password shorter than {MIN_PASSWORD_LENGTH} characters"
                password = secrets.token_urlsafe(8)
                messages.append(
                    f"Row {line}: {email} {reason}, so a temporary password was created: {password} "
                    "Share it with them and ask them to change it after signing in."
                )

            user = User()
            user.register_end_user(full_name, email, password)
            db.session.add(user)
            db.session.flush()  # gives the user an id for the group/project step
            created += 1
            users.append(user)
        except IndexError:
            invalid += 1
            messages.append(f"Row {line}: Malformed row. Skipped.")
        except Exception as e:
            db.session.rollback()
            invalid += 1
            messages.append(f"Row {line}: Error processing row: {str(e)}. Skipped.")
    db.session.commit()
    return users, created, duplicates, invalid, messages


def _add_imported_to_group(users, group_id):
    group = db.session.get(UserGroup, group_id)
    if not group:
        flash("Selected user group not found for assignment.", 'warning')
        return
    added, work = 0, None
    for user in users:
        if not UserGroupMembership.query.filter_by(user_id=user.id, group_id=group.id).first():
            db.session.add(UserGroupMembership(user_id=user.id, group_id=group.id))
            db.session.commit()
            # Same as adding someone on the group page: they get their share of the group's work.
            result = assign_questions_to_new_group_member(user, group)
            work = result if work is None else work.merge(result)
            added += 1
    if added:
        flash(f"{added} users assigned to group '{group.name}'.", 'success')
        flash_distribution(work)
    else:
        flash(f"All users from CSV are already in group '{group.name}'.", 'info')


def _give_imported_project(users, project_id):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Selected project not found for assignment.", 'warning')
        return

    if project.is_annotation_project:
        if not AnnotationTask.query.filter_by(project_id=project.id).first():
            flash(f"No tasks found for project '{project.name}'. Hand out tasks first.", 'warning')
            return
        everyone = {u.id: u for u in distribution.eligible_users(project)}
        everyone.update({u.id: u for u in users})
        result = distribution.assign_open_slots(project, users=list(everyone.values()))
        flash_distribution(result)
        if not result.assignments_created:
            flash(f"No open tasks were left to assign in '{project.name}'.", 'info')
        return

    # Surveys: every imported person gets every question.
    added = 0
    for user in users:
        for question in project.questions:
            if not UserQuestionAssignment.query.filter_by(user_id=user.id, question_id=question.id).first():
                db.session.add(UserQuestionAssignment(
                    user_id=user.id, question_id=question.id,
                    group_project_id=f"imported-{project.id}",  # not tied to a group
                ))
                added += 1
    db.session.commit()
    if added:
        flash(f"{added} questions assigned to users for project '{project.name}'.", 'success')
    else:
        flash(f"All users from CSV are already assigned questions in project '{project.name}'.", 'info')


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

        set_up_new_project(project, assigned_group_id)

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
    progress = {p.id: project_progress(p) for p in projects}
    return render_template("admin/view_projects.html", projects=projects, progress=progress)


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
