import csv
import io
from flask import send_file, make_response
from .models import QuestionAnswer
from functools import wraps
from flask import Blueprint, request, redirect, url_for, flash, render_template, session, abort

from .models import Project, Question, User, UserQuestionAssignment, TaskAssignment, AnnotationTask, ProjectImage
from .extns import db

end_user = Blueprint('user', __name__, url_prefix='/user')


def user_required(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        if 'id' not in session:
            abort(403)

        if session['role'] != 'end-user':
            abort(403)

        return func(*args, **kwargs)

    return decorated_function


@end_user.route("/dashboard")
def dashboard():
    if 'id' in session:
        user_id = session['id']
        user = User.query.get(user_id)
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
    user = User.query.get(user_id)

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

            # For annotation projects, get the count of assigned images
            if gp['project'].is_annotation_project:
                # Count unique images assigned to this user for this project
                assigned_images = db.session.query(ProjectImage.id).distinct().join(
                    AnnotationTask, ProjectImage.id == AnnotationTask.image_id
                ).join(
                    TaskAssignment, AnnotationTask.id == TaskAssignment.task_id
                ).filter(
                    TaskAssignment.user_id == user_id,
                    AnnotationTask.project_id == gp['project'].id
                ).count()
                gp['annotation_tasks_count'] = assigned_images

        return render_template('end-user/my_projects.html',
                               group_projects=group_projects)
    else:
        flash('User not found', 'danger')
        return redirect(url_for('user.my_projects'))


@end_user.route("/download-project/<int:project_id>", methods=['GET'])
@user_required
def csv_download(project_id):
    project = Project.query.get(project_id)
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
                file_path = f"http://localhost:5000/static/uploads/{question.file_path}"
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
    project = Project.query.get(project_id)
    if request.method == 'POST':
        for key, value in request.form.items():
            if key.startswith('answer_'):
                _, question_id = key.split('_')
                question = Question.query.get(question_id)
                user = User.query.get(session['id'])
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
                    question = Question.query.get(question_id)
                    user = User.query.get(user_id)
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
    """View assigned annotation tasks grouped by image"""
    user_id = session["id"]

    # Get all annotation task assignments for this user
    assignments = TaskAssignment.query.filter_by(user_id=user_id).all()

    # Group by project and then by image
    projects_data = {}
    for assignment in assignments:
        task = assignment.task
        project = task.project
        image = task.image

        if project.id not in projects_data:
            projects_data[project.id] = {
                'project': project,
                'images': {},
                'total_images': 0,
                'completed_images': 0,
                'total_tasks': 0,
                'completed_tasks': 0
            }

        # Group tasks by image
        if image.id not in projects_data[project.id]['images']:
            projects_data[project.id]['images'][image.id] = {
                'image': image,
                'assignments': [],
                'completed_count': 0,
                'total_count': 0
            }
            projects_data[project.id]['total_images'] += 1

        # Add assignment to image group
        projects_data[project.id]['images'][image.id]['assignments'].append(assignment)
        projects_data[project.id]['images'][image.id]['total_count'] += 1
        projects_data[project.id]['total_tasks'] += 1

        if assignment.is_completed:
            projects_data[project.id]['images'][image.id]['completed_count'] += 1
            projects_data[project.id]['completed_tasks'] += 1

    # Calculate completed images (all questions for an image are done)
    for project_id in projects_data:
        for image_id in projects_data[project_id]['images']:
            image_data = projects_data[project_id]['images'][image_id]
            if image_data['completed_count'] == image_data['total_count']:
                projects_data[project_id]['completed_images'] += 1

    return render_template("user/annotation_tasks.html", projects_data=projects_data)


@end_user.route("/annotation-task/<int:assignment_id>", methods=["GET", "POST"])
@user_required
def annotation_task(assignment_id):
    """Complete an annotation task"""
    assignment = TaskAssignment.query.get_or_404(assignment_id)

    # Verify this assignment belongs to the current user
    if assignment.user_id != session["id"]:
        abort(403)

    task = assignment.task
    image = task.image
    question = task.question
    project = task.project

    if request.method == "POST":
        answer_text = request.form.get("answer")

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
            return redirect(url_for("user.annotation_tasks"))
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

    image = ProjectImage.query.get_or_404(image_id)
    project = Project.query.get_or_404(project_id)

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

    print(f"DEBUG: Total uncompleted questions: {total_uncompleted_questions}")
    print(f"DEBUG: Batch size: {batch_size}")
    print(f"DEBUG: Current batch index: {batch_index}")

    if not all_uncompleted_assignments and batch_index == 0:
        flash("No new tasks found for this image!", "info")
        return redirect(url_for("user.annotation_tasks"))
    elif not all_uncompleted_assignments and batch_index > 0:
        flash("All questions for this image have been answered!", "success")
        return redirect(url_for("user.annotation_tasks"))

    start_index = batch_index * batch_size
    end_index = start_index + batch_size

    print(f"DEBUG: Start index: {start_index}, End index: {end_index}")

    current_batch_assignments = all_uncompleted_assignments[start_index:end_index]

    print(f"DEBUG: Assignments in current batch: {len(current_batch_assignments)}")

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

        # Determine next page
        next_batch_index = batch_index + 1
        if next_batch_index * batch_size < total_uncompleted_questions:
            return redirect(url_for("user.annotate_image",
                                    image_id=image.id,
                                    project_id=project.id,
                                    batch_index=next_batch_index))
        else:
            flash("All questions for this image have been answered!", "success")
            return redirect(url_for("user.annotation_tasks"))

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
