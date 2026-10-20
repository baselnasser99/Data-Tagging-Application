import logging
from datetime import datetime, timedelta

from flask import Blueprint, request, redirect, url_for, flash, render_template, session, abort
from .models import Project, Question, User, UserQuestionAssignment, TaskAssignment, AnnotationTask, ProjectImage, QuestionAnswer
from .extns import db

from .permissions import user_required
from .auth import MIN_PASSWORD_LENGTH
from werkzeug.security import generate_password_hash, check_password_hash

# Annotators' pages (on screen: members). The URLs keep their original /user prefix.
annotation = Blueprint('annotation', __name__, url_prefix='/user')

@annotation.route("/dashboard")
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

            answered_this_week = QuestionAnswer.query.filter(
                QuestionAnswer.user_id == user_id,
                QuestionAnswer.submitted_date >= datetime.now() - timedelta(days=7)).count()

            return render_template('end-user/dashboard.html',
                                   group_projects=_my_group_projects(user_id),
                                   assigned_projects=group_projects_count,
                                   group_memberships=group_memberships,
                                   solved_questions=total_solved,
                                   unanswered_questions=unanswered_questions,
                                   available_projects=available_projects,
                                   answered_this_week=answered_this_week,
                                   next_up=_next_open_task(user_id))
        else:
            flash('User not found', 'danger')
            return redirect(url_for('annotation.my_projects'))
    else:
        flash('No active session', 'warning')
        return redirect(url_for('annotation.my_projects'))


# Individual project assignments removed - only group assignments supported


@annotation.route("/my-projects")
@user_required
def my_projects():
    return render_template('end-user/my_projects.html', group_projects=_my_group_projects(session['id']))


def _next_open_task(user_id):
    """The member's first unanswered task, with the page that answers it; None when nothing is open."""
    nxt = TaskAssignment.query.join(AnnotationTask).filter(
        TaskAssignment.user_id == user_id, TaskAssignment.is_completed == False,
    ).order_by(AnnotationTask.project_id, AnnotationTask.image_id, AnnotationTask.question_id).first()
    if nxt is None:
        return None
    task = nxt.task
    if task.project.hand_out_per_item:
        url = url_for("annotation.annotate_image", image_id=task.image_id, project_id=task.project_id)
    else:
        url = url_for("annotation.annotation_task", assignment_id=nxt.id)
    return {"url": url, "project": task.project, "item": task.image, "question": task.question}


def _my_progress(user_id, project_id):
    """(done, total) of the member's own tasks in one project."""
    mine = TaskAssignment.query.join(AnnotationTask).filter(
        TaskAssignment.user_id == user_id, AnnotationTask.project_id == project_id)
    return mine.filter(TaskAssignment.is_completed == True).count(), mine.count()


def _my_group_projects(user_id):
    """The user's projects through their groups, each with their own progress (done, total)."""
    user = db.session.get(User, user_id)
    group_projects = []
    for membership in user.group_memberships:
        for group_project in membership.group.assigned_projects:
            project = group_project.project
            gp = {
                'project': project,
                'group_name': membership.group.name,
                'group_project_id': f"{membership.group.id}-{project.id}",
            }
            if project.is_annotation_project:
                mine = TaskAssignment.query.join(AnnotationTask).filter(
                    TaskAssignment.user_id == user_id, AnnotationTask.project_id == project.id)
                gp['task_total'] = gp['total'] = mine.count()
                gp['task_left'] = mine.filter(TaskAssignment.is_completed == False).count()
                gp['done'] = gp['total'] - gp['task_left']
            else:
                assigned = UserQuestionAssignment.query.filter_by(
                    user_id=user_id, group_project_id=gp['group_project_id']).all()
                gp['total'] = len(assigned)
                gp['done'] = sum(1 for a in assigned if QuestionAnswer.query.filter_by(
                    user_id=user_id, question_id=a.question_id).first())
                project.total_questions = gp['total']
                project.answered = gp['total'] > 0 and gp['done'] == gp['total']
            group_projects.append(gp)
    return group_projects


@annotation.route("/profile")
@user_required
def profile():
    return render_template('end-user/profile.html')


@annotation.route("/change-password", methods=['GET', 'POST'])
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
            return redirect(url_for('annotation.profile'))

        # Verify current password
        if not check_password_hash(user.password_hash, current_password):
            flash('Current password is incorrect', 'danger')
            return redirect(url_for('annotation.change_password'))

        # Validate new password
        if new_password != confirm_password:
            flash('New passwords do not match', 'danger')
            return redirect(url_for('annotation.change_password'))

        if len(new_password) < MIN_PASSWORD_LENGTH:
            flash(f'Password must be at least {MIN_PASSWORD_LENGTH} characters long', 'danger')
            return redirect(url_for('annotation.change_password'))

        # Update password
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        logging.getLogger("audit").info("PASSWORD_CHANGED user_id=%s", user.id)
        flash('Password changed successfully', 'success')
        return redirect(url_for('annotation.profile'))

    return render_template('end-user/change_password.html')


@annotation.route("/group-project/<group_project_id>", methods=['GET', 'POST'])
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
        return redirect(url_for('annotation.my_projects'))

    # Get the project details from the first assignment
    project = assigned_questions[0].question.belongs_to

    if request.method == 'POST':
        for key, value in request.form.items():
            if key.startswith('answer_'):
                question_id = key.removeprefix('answer_')
                if not question_id.isdigit():
                    continue  # a tampered field name; ignore it instead of crashing
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
        return redirect(url_for('annotation.my_projects'))

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


@annotation.route("/annotation-tasks")
@user_required
def annotation_tasks():
    """View assigned annotation tasks, one card per item; ?project_id= shows one project only."""
    user_id = session["id"]
    project_id = request.args.get("project_id", type=int)

    query = TaskAssignment.query.filter_by(user_id=user_id).join(AnnotationTask)
    if project_id:
        query = query.filter(AnnotationTask.project_id == project_id)
    assignments = query.order_by(AnnotationTask.project_id, AnnotationTask.image_id, AnnotationTask.question_id).all()

    # One card per task; per-item projects keep one card per item (answered in one sitting).
    cards = []
    for a in assignments:
        prev = cards[-1][-1] if cards else None
        if (a.task.project.hand_out_per_item and prev is not None
                and prev.task.project_id == a.task.project_id and prev.task.image_id == a.task.image_id):
            cards[-1].append(a)
        else:
            cards.append([a])

    # Name the project only from the user's own assignments, so other projects' names never leak.
    project = assignments[0].task.project if project_id and assignments else None
    return render_template("user/annotation_tasks.html", cards=cards,
                           project=project, filtered=bool(project_id))


def _next_item(user_id, project_id):
    """After a task (or a per-item project's item) is done, open the user's next unanswered one."""
    nxt = TaskAssignment.query.join(AnnotationTask).filter(
        TaskAssignment.user_id == user_id,
        AnnotationTask.project_id == project_id,
        TaskAssignment.is_completed == False,
    ).order_by(AnnotationTask.image_id, AnnotationTask.question_id).first()
    if nxt and nxt.task.project.hand_out_per_item:
        return redirect(url_for("annotation.annotate_image", image_id=nxt.task.image_id, project_id=project_id))
    if nxt:
        return redirect(url_for("annotation.annotation_task", assignment_id=nxt.id))
    flash("You have finished all your tasks in this project.", "success")
    return redirect(url_for("annotation.annotation_tasks", project_id=project_id))


def _save_answer(assignment, text):
    """Store one answer and mark the assignment done; the task is done once it has enough answers."""
    db.session.add(QuestionAnswer(user_id=assignment.user_id, question_id=assignment.task.question_id,
                                  answer=text, task_assignment_id=assignment.id))
    assignment.is_completed = True
    assignment.completed_date = db.func.current_timestamp()
    assignment.task.current_answers += 1
    if assignment.task.current_answers >= assignment.task.required_answers:
        assignment.task.is_completed = True


@annotation.route("/annotation-task/<int:assignment_id>", methods=["GET", "POST"])
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
            return redirect(url_for("annotation.annotation_tasks"))

        if answer_text:
            _save_answer(assignment, answer_text)
            db.session.commit()
            flash("Answer submitted successfully!", "success")
            return _next_item(session["id"], project.id)
        else:
            flash("Please provide an answer!", "error")

    done, total = _my_progress(session["id"], project.id)
    return render_template("user/annotation_task.html",
                         assignment=assignment,
                         task=task,
                         image=image,
                         question=question,
                         project=project,
                         done=done,
                         total=total)


@annotation.route("/annotate-image/<int:image_id>/<int:project_id>", methods=["GET", "POST"])
@annotation.route("/annotate-image/<int:image_id>/<int:project_id>/<int:batch_index>", methods=["GET", "POST"])
@user_required
def annotate_image(image_id, project_id, batch_index=0):
    """Per-item projects: answer an item's questions on one page, a few at a time."""
    user_id = session["id"]
    image = db.get_or_404(ProjectImage, image_id)
    project = db.get_or_404(Project, project_id)
    batch_size = project.questions_per_image if project.questions_per_image > 0 else 5

    open_assignments = TaskAssignment.query.join(AnnotationTask).filter(
        TaskAssignment.user_id == user_id,
        AnnotationTask.image_id == image_id,
        AnnotationTask.project_id == project_id,
        TaskAssignment.is_completed == False,
    ).order_by(AnnotationTask.question_id).all()
    total_uncompleted_questions = len(open_assignments)
    current_batch_assignments = open_assignments[batch_index * batch_size:(batch_index + 1) * batch_size]

    if not current_batch_assignments:
        if total_uncompleted_questions or batch_index > 0:
            flash("All questions for this image have been answered!", "success")
        else:
            flash("No new tasks found for this image!", "info")
        return redirect(url_for("annotation.annotation_tasks"))

    if request.method == "POST":
        answered = 0
        for assignment in current_batch_assignments:
            text = (request.form.get(f"answer_{assignment.id}") or "").strip()
            if text:
                _save_answer(assignment, text)
                answered += 1
        db.session.commit()

        if answered:
            flash(f"Successfully submitted {answered} answers for this batch!", "success")
        else:
            flash("No new answers were submitted in this batch.", "warning")

        # Answered questions drop out of the open list, so the next batch
        # always starts at the beginning of what is left.
        if total_uncompleted_questions - answered > 0:
            return redirect(url_for("annotation.annotate_image", image_id=image.id, project_id=project.id))
        return _next_item(user_id, project.id)

    done, total = _my_progress(user_id, project.id)
    return render_template("user/annotate_image.html",
                         image=image,
                         project=project,
                         assignments=current_batch_assignments, # Pass only the current batch
                         batch_index=batch_index,
                         total_uncompleted_questions=total_uncompleted_questions,
                         batch_size=batch_size,
                         done=done,
                         total=total)
