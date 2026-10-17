import csv
import io
from flask import send_file, make_response, Blueprint, request, redirect, url_for, flash, render_template, session, abort
from .models import Project, Question, User, UserQuestionAssignment, TaskAssignment, AnnotationTask, ProjectImage, QuestionAnswer
from .extns import db
import logging

from .permissions import user_required
from werkzeug.security import generate_password_hash, check_password_hash

end_user = Blueprint('user', __name__, url_prefix='/user')
log = logging.getLogger(__name__)

@end_user.route("/dashboard")
@user_required
def dashboard():
    if 'id' in session:
        user_id = session['id']
        user = db.session.get(User, user_id)
        if user:
            # Count group memberships
            group_memberships = len(user.group_memberships)

            # Count group projects assigned (both survey and annotation)
            group_projects_count = 0
            for membership in user.group_memberships:
                group_projects_count += len(membership.group.assigned_projects)

            # Count total assigned tasks (survey questions + annotation tasks)
            survey_questions = UserQuestionAssignment.query.filter_by(user_id=user_id).count()
            annotation_tasks = TaskAssignment.query.filter_by(user_id=user_id).count()
            total_assigned_tasks = survey_questions + annotation_tasks

            # Count solved questions/tasks
            survey_answers = QuestionAnswer.query.filter_by(user_id=user_id).filter(
                QuestionAnswer.task_assignment_id.is_(None)
            ).count()
            annotation_answers = QuestionAnswer.query.filter_by(user_id=user_id).filter(
                QuestionAnswer.task_assignment_id.isnot(None)
            ).count()
            total_solved = survey_answers + annotation_answers

            # Calculate unanswered (ensure it's not negative)
            unanswered_questions = max(0, total_assigned_tasks - total_solved)

            # Count available projects (projects with pending tasks)
            available_projects = 0
            if total_assigned_tasks > total_solved:
                available_projects = group_projects_count

            return render_template('end-user/dashboard.html',
                                   assigned_projects=group_projects_count,
                                   group_memberships=group_memberships,
                                   solved_questions=total_solved,
                                   unanswered_questions=unanswered_questions,
                                   available_projects=available_projects)
        else:
            flash('User not found', 'danger')
            return redirect(url_for('user.my_projects'))
    else:
        flash('No active session', 'warning')
        return redirect(url_for('user.my_projects'))


# Individual project assignments removed - only group assignments supported


@end_user.route("/my-projects")
@user_required
def my_projects():
    user_id = session['id']
    user = db.session.get(User, user_id)

    if user:
        # Get group-assigned projects (only assignment method now)
        group_projects = []
        for membership in user.group_memberships:
            for group_project in membership.group.assigned_projects:
                # Create a project-like object for group assignments
                project_info = {
                    'project': group_project.project,
                    'group_name': membership.group.name,
                    'is_group_project': True,
                    'group_project_id': f"{membership.group.id}-{group_project.project.id}"
                }
                group_projects.append(project_info)

        # Process group projects
        for gp in group_projects:
            # Get questions assigned to this user for this group project
            assigned_questions = UserQuestionAssignment.query.filter_by(
                user_id=user_id,
                group_project_id=str(gp['group_project_id'])  # Ensure it's a string
            ).all()

            gp['project'].total_questions = len(assigned_questions)
            gp['project'].is_group_project = True
            gp['project'].group_name = gp['group_name']

            # Check how many of assigned questions are answered
            answered_count = 0
            for assignment in assigned_questions:
                fnd = QuestionAnswer.query.filter_by(
                    user_id=user_id, question_id=assignment.question_id).first()
                if fnd:
                    answered_count += 1

            if answered_count == len(assigned_questions) and len(assigned_questions) > 0:
                gp['project'].answered = True
            else:
                gp['project'].answered = False

            # For annotation projects, count this user's tasks and how many are left
            if gp['project'].is_annotation_project:
                mine = TaskAssignment.query.join(AnnotationTask).filter(
                    TaskAssignment.user_id == user_id,
                    AnnotationTask.project_id == gp['project'].id,
                )
                gp['task_total'] = mine.count()
                gp['task_left'] = mine.filter(TaskAssignment.is_completed == False).count()

        return render_template('end-user/my_projects.html',
                               group_projects=group_projects)
    else:
        flash('User not found', 'danger')
        return redirect(url_for('user.my_projects'))


@end_user.route("/download-project/<int:project_id>", methods=['GET'])
@user_required
def csv_download(project_id):
    project = db.session.get(Project, project_id)
    if project:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Project Name', project.name])
        writer.writerow(['Project Description', project.desc])

        writer.writerow(['Question', 'Answer', 'File Path'])

        for question in project.questions:
            question_answers = QuestionAnswer.query.filter_by(
                question_id=question.id).first()
            answer = question_answers.answer if question_answers else None
            file_path = 'No File'
            if question.file_path:
                file_path = url_for('static', filename=f'uploads/{question.file_path}', _external=True)
            writer.writerow([question.question, answer, file_path])

        output.seek(0)

        # Convert StringIO to BytesIO for send_file
        mem = io.BytesIO()
        mem.write(output.getvalue().encode())
        mem.seek(0)

        response = make_response(send_file(
            mem, as_attachment=True, download_name='questions.txt', mimetype='text/plain'))
        return response
    else:
        flash('Project not found', 'danger')
        return redirect(url_for('admin.view_projects'))


@end_user.route("/project/<int:project_id>", methods=['GET', 'POST'])
@user_required
def view_project(project_id):
    project = db.session.get(Project, project_id)
    if request.method == 'POST':
        for key, value in request.form.items():
            if key.startswith('answer_'):
                _, question_id = key.split('_')
                question = db.session.get(Question, question_id)
                user = db.session.get(User, session['id'])
                answer = value
                qa = QuestionAnswer(user_id=user.id, question_id=question.id, answer=answer)

                db.session.add(qa)
                db.session.commit()
        flash('Answers submitted successfully', 'success')
        return redirect(url_for('user.my_projects'))
    elif project:
        return render_template('end-user/project.html', project=project)
    else:
        flash('Project not found', 'danger')
        return redirect(url_for('user.my_projects'))


@end_user.route("/profile")
@user_required
def profile():
    return render_template('end-user/profile.html')


@end_user.route("/change-password", methods=['GET', 'POST'])
@user_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        user_id = session['id']
        user = db.session.get(User, user_id)

        if not user:
            flash('User not found', 'danger')
            return redirect(url_for('user.profile'))

        # Verify current password
        if not check_password_hash(user.password_hash, current_password):
            flash('Current password is incorrect', 'danger')
            return redirect(url_for('user.change_password'))

        # Validate new password
        if new_password != confirm_password:
            flash('New passwords do not match', 'danger')
            return redirect(url_for('user.change_password'))

        if len(new_password) < 6:
            flash('Password must be at least 6 characters long', 'danger')
            return redirect(url_for('user.change_password'))

        # Update password
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        flash('Password changed successfully', 'success')
        return redirect(url_for('user.profile'))

    return render_template('end-user/change_password.html')


@end_user.route("/group-project/<group_project_id>", methods=['GET', 'POST'])
@user_required
def view_group_project(group_project_id):
    user_id = session['id']

    # Get questions assigned to this user for this group project
    assigned_questions = UserQuestionAssignment.query.filter_by(
        user_id=user_id,
        group_project_id=str(group_project_id)  # Ensure it's a string
    ).all()

    if not assigned_questions:
        flash('No questions assigned to you for this project', 'warning')
        return redirect(url_for('user.my_projects'))

    # Get the project details from the first assignment
    project = assigned_questions[0].question.belongs_to

    if request.method == 'POST':
        for key, value in request.form.items():
            if key.startswith('answer_'):
                _, question_id = key.split('_')
                question_id = int(question_id)

                # Verify this question is assigned to the user
                assignment = UserQuestionAssignment.query.filter_by(
                    user_id=user_id,
                    question_id=question_id,
                    group_project_id=group_project_id
                ).first()

                if assignment:
                    question = db.session.get(Question, question_id)
                    user = db.session.get(User, user_id)
                    answer = value

                    # Check if answer already exists
                    existing_answer = QuestionAnswer.query.filter_by(
                        user_id=user.id, question_id=question.id).first()

                    if existing_answer:
                        existing_answer.answer = answer
                    else:
                        qa = QuestionAnswer(user_id=user.id, question_id=question.id, answer=answer)
                        db.session.add(qa)

                    db.session.commit()

        flash('Answers submitted successfully', 'success')
        return redirect(url_for('user.my_projects'))

    # Get only the questions assigned to this user
    questions = [assignment.question for assignment in assigned_questions]

    # Create a project-like object with only assigned questions
    project_data = {
        'id': project.id,
        'name': project.name,
        'desc': project.desc,
        'questions': questions,
        'is_group_project': True,
        'group_project_id': group_project_id
    }

    return render_template('end-user/group_project.html', project=project_data)


@end_user.route("/annotation-tasks")
@user_required
def annotation_tasks():
    """View assigned annotation tasks, one card per item; ?project_id= shows one project only."""
    user_id = session["id"]
    project_id = request.args.get("project_id", type=int)

    query = TaskAssignment.query.filter_by(user_id=user_id).join(AnnotationTask)
    if project_id:
        query = query.filter(AnnotationTask.project_id == project_id)
    assignments = query.order_by(AnnotationTask.project_id, AnnotationTask.image_id, AnnotationTask.question_id).all()

    # Name the project only from the user's own assignments, so other projects' names never leak.
    project = assignments[0].task.project if project_id and assignments else None
    return render_template("user/annotation_tasks.html", assignments=assignments,
                           project=project, filtered=bool(project_id))


def _next_item(user_id, project_id):
    """After an item is done, open the user's next unanswered item in the same project."""
    nxt = TaskAssignment.query.join(AnnotationTask).filter(
        TaskAssignment.user_id == user_id,
        AnnotationTask.project_id == project_id,
        TaskAssignment.is_completed == False,
    ).order_by(AnnotationTask.image_id, AnnotationTask.question_id).first()
    if nxt:
        return redirect(url_for("user.annotate_image", image_id=nxt.task.image_id, project_id=project_id))
    flash("You have finished all your tasks in this project.", "success")
    return redirect(url_for("user.annotation_tasks", project_id=project_id))


@end_user.route("/annotation-task/<int:assignment_id>", methods=["GET", "POST"])
@user_required
def annotation_task(assignment_id):
    """Complete an annotation task"""
    assignment = db.get_or_404(TaskAssignment, assignment_id)

    # Verify this assignment belongs to the current user
    if assignment.user_id != session["id"]:
        abort(403)

    task = assignment.task
    image = task.image
    question = task.question
    project = task.project

    if request.method == "POST":
        answer_text = (request.form.get("answer") or "").strip()

        if assignment.is_completed:
            flash("You have already answered this task.", "info")
            return redirect(url_for("user.annotation_tasks"))

        if answer_text:
            # Save the answer linked to this specific task assignment
            answer = QuestionAnswer(
                user_id=session["id"],
                question_id=question.id,
                answer=answer_text,
                task_assignment_id=assignment.id
            )
            db.session.add(answer)

            # Mark assignment as completed
            assignment.is_completed = True
            assignment.completed_date = db.func.current_timestamp()

            # Update task completion status
            task.current_answers += 1
            if task.current_answers >= task.required_answers:
                task.is_completed = True

            db.session.commit()
            flash("Answer submitted successfully!", "success")
            return _next_item(session["id"], project.id)
        else:
            flash("Please provide an answer!", "error")

    return render_template("user/annotation_task.html",
                         assignment=assignment,
                         task=task,
                         image=image,
                         question=question,
                         project=project)


@end_user.route("/annotate-image/<int:image_id>/<int:project_id>", methods=["GET", "POST"])
@end_user.route("/annotate-image/<int:image_id>/<int:project_id>/<int:batch_index>", methods=["GET", "POST"])
@user_required
def annotate_image(image_id, project_id, batch_index=0):
    """Complete questions for a specific image in batches"""
    user_id = session["id"]

    image = db.get_or_404(ProjectImage, image_id)
    project = db.get_or_404(Project, project_id)

    # Determine batch size (e.g., questions_per_image from project settings)
    batch_size = project.questions_per_image if project.questions_per_image > 0 else 5

    # --- Synchronization Step: Mark assignments as completed if answers exist ---
    # Get all assignments for this user, image, and project (completed or not)
    all_assignments_for_image = TaskAssignment.query.join(AnnotationTask).filter(
        TaskAssignment.user_id == user_id,
        AnnotationTask.image_id == image_id,
        AnnotationTask.project_id == project_id
    ).all()

    for assignment in all_assignments_for_image:
        if not assignment.is_completed:
            # Check if an answer exists for this assignment
            existing_answer = QuestionAnswer.query.filter_by(
                task_assignment_id=assignment.id,
                user_id=user_id,
                question_id=assignment.task.question_id
            ).first()
            if existing_answer:
                assignment.is_completed = True
                assignment.completed_date = db.func.current_timestamp()
                # Also update the parent AnnotationTask's current_answers if not already updated
                if assignment.task.current_answers < assignment.task.required_answers:
                    assignment.task.current_answers += 1
                    if assignment.task.current_answers >= assignment.task.required_answers:
                        assignment.task.is_completed = True
                db.session.add(assignment) # Mark for update
                db.session.add(assignment.task) # Mark task for update
    db.session.commit() # Commit these synchronization updates

    # --- End Synchronization Step ---

    # Get all UNCOMPLETED assignments for this user, image, and project AFTER synchronization
    all_uncompleted_assignments = TaskAssignment.query.join(AnnotationTask).filter(
        TaskAssignment.user_id == user_id,
        AnnotationTask.image_id == image_id,
        AnnotationTask.project_id == project_id,
        TaskAssignment.is_completed == False
    ).order_by(AnnotationTask.question_id).all()

    total_uncompleted_questions = len(all_uncompleted_assignments)

    log.debug(f"Total uncompleted questions: {total_uncompleted_questions}")
    log.debug(f"Batch size: {batch_size}")
    log.debug(f"Current batch index: {batch_index}")
    if not all_uncompleted_assignments and batch_index == 0:
        flash("No new tasks found for this image!", "info")
        return redirect(url_for("user.annotation_tasks"))
    elif not all_uncompleted_assignments and batch_index > 0:
        flash("All questions for this image have been answered!", "success")
        return redirect(url_for("user.annotation_tasks"))

    start_index = batch_index * batch_size
    end_index = start_index + batch_size

    log.debug(f"Start index: {start_index}, End index: {end_index}")
    current_batch_assignments = all_uncompleted_assignments[start_index:end_index]

    log.debug(f"Assignments in current batch: {len(current_batch_assignments)}")
    if not current_batch_assignments and total_uncompleted_questions > 0:
        flash("All questions for this image have been answered!", "success")
        return redirect(url_for("user.annotation_tasks"))
    elif not current_batch_assignments and total_uncompleted_questions == 0:
        flash("No tasks found for this image!", "info")
        return redirect(url_for("user.annotation_tasks"))

    if request.method == "POST":
        answers_submitted_in_batch = 0
        
        for assignment in current_batch_assignments:
            answer_key = f"answer_{assignment.id}"
            answer_text = request.form.get(answer_key)

            if answer_text and answer_text.strip():
                existing_answer = QuestionAnswer.query.filter_by(
                    task_assignment_id=assignment.id,
                    user_id=user_id,
                    question_id=assignment.task.question_id
                ).first()

                if existing_answer:
                    existing_answer.answer = answer_text.strip()
                else:
                    answer = QuestionAnswer(
                        user_id=user_id,
                        question_id=assignment.task.question_id,
                        answer=answer_text.strip(),
                        task_assignment_id=assignment.id
                    )
                    db.session.add(answer)

                assignment.is_completed = True
                assignment.completed_date = db.func.current_timestamp()

                assignment.task.current_answers += 1
                if assignment.task.current_answers >= assignment.task.required_answers:
                    assignment.task.is_completed = True

                answers_submitted_in_batch += 1
        
        db.session.commit()

        if answers_submitted_in_batch > 0:
            flash(f"Successfully submitted {answers_submitted_in_batch} answers for this batch!", "success")
        else:
            flash("No new answers were submitted in this batch.", "warning")

        # Answered questions drop out of the "uncompleted" list, so the next
        # batch always starts at the beginning of what is left.
        if total_uncompleted_questions - answers_submitted_in_batch > 0:
            return redirect(url_for("user.annotate_image", image_id=image.id, project_id=project.id))
        return _next_item(user_id, project.id)

    total_pages = (total_uncompleted_questions + batch_size - 1) // batch_size
    has_next_page = (batch_index + 1) < total_pages
    has_prev_page = batch_index > 0

    return render_template("user/annotate_image.html",
                         image=image,
                         project=project,
                         assignments=current_batch_assignments, # Pass only the current batch
                         batch_index=batch_index,
                         total_uncompleted_questions=total_uncompleted_questions,
                         batch_size=batch_size,
                         has_next_page=has_next_page,
                         has_prev_page=has_prev_page)
