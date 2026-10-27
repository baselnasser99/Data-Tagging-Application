from datetime import timedelta

from flask import Blueprint, request, redirect, url_for, flash, render_template, abort
from flask_login import current_user
from sqlalchemy import update

from .auth import change_own_password
from .extns import db
from .models import (answer_problem, Project, User, UserQuestionAssignment, TaskAssignment, AnnotationTask, ProjectImage, QuestionAnswer,
                     GroupProject, UserGroupMembership, db_now)
from .permissions import user_required
from .stats import count_answered

# Annotators' pages (on screen: members). The URLs keep their original /user prefix.
annotation = Blueprint('annotation', __name__, url_prefix='/user')

@annotation.route("/dashboard")
@user_required
def dashboard():
    user_id = current_user.id

    # Group projects assigned (both survey and annotation)
    group_projects_count = sum(len(m.group.assigned_projects) for m in current_user.group_memberships)

    # Total assigned tasks (survey questions + annotation tasks)
    total_assigned_tasks = (UserQuestionAssignment.query.filter_by(user_id=user_id).count()
                            + TaskAssignment.query.filter_by(user_id=user_id).count())
    # Every answer counts: survey answers and annotation answers alike
    total_solved = QuestionAnswer.query.filter_by(user_id=user_id).count()

    answered_this_week = QuestionAnswer.query.filter(
        QuestionAnswer.user_id == user_id,
        QuestionAnswer.submitted_date >= db_now() - timedelta(days=7)).count()

    return render_template('end-user/dashboard.html',
                           group_projects=_my_group_projects(user_id),
                           assigned_projects=group_projects_count,
                           group_memberships=len(current_user.group_memberships),
                           solved_questions=total_solved,
                           unanswered_questions=max(0, total_assigned_tasks - total_solved),
                           # projects with pending tasks
                           available_projects=group_projects_count if total_assigned_tasks > total_solved else 0,
                           answered_this_week=answered_this_week,
                           next_up=_next_open_task(user_id))


@annotation.route("/my-projects")
@user_required
def my_projects():
    return render_template('end-user/my_projects.html', group_projects=_my_group_projects(current_user.id))


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
                gp['done'] = count_answered(assigned)
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
        error = change_own_password(db.session.get(User, current_user.id), request.form.get('current_password'),
                                    request.form.get('new_password'), request.form.get('confirm_password'))
        if error:
            flash(error, 'danger')
            return redirect(url_for('annotation.change_password'))
        flash('Password changed successfully', 'success')
        return redirect(url_for('annotation.profile'))

    return render_template('end-user/change_password.html')


def _still_holds_key(user_id, key):
    """May the member still use survey rows held under ``key``?

    Answered rows are kept when someone leaves a group (see groups.py), so a
    "<group id>-<project id>" key works only while they are still in that group
    and the group still has the project. CSV-imported keys belong to no group.
    """
    group_id, _, project_id = key.partition("-")
    if not (group_id.isdigit() and project_id.isdigit()):
        return True
    return (GroupProject.query.filter_by(group_id=int(group_id), project_id=int(project_id)).first() is not None
            and UserGroupMembership.query.filter_by(user_id=user_id, group_id=int(group_id)).first() is not None)


@annotation.route("/group-project/<group_project_id>", methods=['GET', 'POST'])
@user_required
def view_group_project(group_project_id):
    user_id = current_user.id

    # Get questions assigned to this user for this group project. A key ends in the project's id
    # ("<group id>-<project id>", "imported-<project id>"): only that project's questions count.
    assigned_questions = UserQuestionAssignment.query.filter_by(
        user_id=user_id,
        group_project_id=str(group_project_id)  # Ensure it's a string
    ).all()
    key_project = group_project_id.rpartition("-")[2]
    if key_project.isdigit():
        assigned_questions = [a for a in assigned_questions
                              if a.question is not None and a.question.project_id == int(key_project)]
    allowed = {a.question_id: a.question for a in assigned_questions}

    if not assigned_questions or not _still_holds_key(user_id, group_project_id):
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

                # Only the questions assigned to this user, of this key's project (see above)
                question = allowed.get(question_id)
                problem = question is not None and answer_problem(question, value)
                if problem:
                    flash(f'"{question.question}": {problem}', 'danger')
                elif question is not None:
                    # Re-submitting replaces the earlier answer
                    existing_answer = QuestionAnswer.query.filter_by(user_id=user_id, question_id=question_id).first()
                    if existing_answer:
                        existing_answer.answer = value
                    else:
                        db.session.add(QuestionAnswer(user_id=user_id, question_id=question_id, answer=value))
                    db.session.commit()

        flash('Answers submitted successfully', 'success')
        return redirect(url_for('annotation.my_projects'))

    # Get only the questions assigned to this user
    questions = [assignment.question for assignment in assigned_questions]

    # What the page shows: the project, with only the questions assigned to this user
    project_data = {
        'name': project.name,
        'desc': project.desc,
        'questions': questions,
        'group_project_id': group_project_id
    }

    return render_template('end-user/group_project.html', project=project_data)


@annotation.route("/annotation-tasks")
@user_required
def annotation_tasks():
    """View assigned annotation tasks, one card per item; ?project_id= shows one project only."""
    user_id = current_user.id
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


CANT_TELL = "Can't tell"


def _save_answer(assignment, text, cant_tell=False):
    """Store one answer and mark the assignment done; the task is done once it has enough answers.

    cant_tell=True: the member couldn't answer. It finishes the task all the same (simplest for
    everyone: nobody else is handed the task), and the export and results leave it out of agreement.

    Returns False, storing nothing, when the assignment was already done: two submits at the same
    moment (a double click, two tabs) both pass the callers' is_completed check, so the database
    decides which one wins. Counts are added up in SQL for the same reason. It is False too when a
    rebalance has just moved the assignment to someone else, so the answer isn't filed under them.
    """
    claimed = db.session.execute(
        update(TaskAssignment)
        .where(TaskAssignment.id == assignment.id, TaskAssignment.user_id == assignment.user_id,
               TaskAssignment.is_completed == False)
        .values(is_completed=True, completed_date=db.func.current_timestamp())
        .execution_options(synchronize_session=False)).rowcount
    if claimed != 1:
        db.session.expire(assignment)
        return False
    db.session.add(QuestionAnswer(user_id=assignment.user_id, question_id=assignment.task.question_id,
                                  answer=CANT_TELL if cant_tell else text, cant_tell=cant_tell,
                                  task_assignment_id=assignment.id))
    task = AnnotationTask.__table__
    # Two statements: MySQL would let a combined SET see the new count and close the task one answer early.
    db.session.execute(update(task).where(task.c.id == assignment.task_id)
                       .values(current_answers=task.c.current_answers + 1))
    db.session.execute(update(task).where(task.c.id == assignment.task_id,
                                          task.c.current_answers >= task.c.required_answers)
                       .values(is_completed=True))
    db.session.expire(assignment)
    db.session.expire(assignment.task)
    return True


@annotation.route("/annotation-task/<int:assignment_id>", methods=["GET", "POST"])
@user_required
def annotation_task(assignment_id):
    """Complete an annotation task"""
    assignment = db.get_or_404(TaskAssignment, assignment_id)

    # Verify this assignment belongs to the current user
    if assignment.user_id != current_user.id:
        abort(403)

    task = assignment.task
    image = task.image
    question = task.question
    project = task.project

    if request.method == "POST":
        answer_text = (request.form.get("answer") or "").strip()

        cant_tell = bool(request.form.get("cant_tell"))
        if assignment.is_completed:
            flash("You have already answered this task.", "info")
            return redirect(url_for("annotation.annotation_tasks"))
        problem = None if cant_tell else answer_problem(question, answer_text)
        if not cant_tell and not answer_text:
            flash("Please provide an answer!", "error")
        elif problem:
            flash(problem, "error")
        elif not _save_answer(assignment, answer_text, cant_tell=cant_tell):
            db.session.rollback()
            flash("You have already answered this task.", "info")
            return redirect(url_for("annotation.annotation_tasks"))
        else:
            db.session.commit()
            flash("Marked as \"can't tell\". Your manager will see it." if cant_tell
                  else "Answer submitted successfully!", "success")
            return _next_item(current_user.id, project.id)

    done, total = _my_progress(current_user.id, project.id)
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
    user_id = current_user.id
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

    if request.method == "POST" and request.form.get("cant_tell"):
        # The item itself is unclear, so every question about it still open for this member
        for assignment in open_assignments:
            _save_answer(assignment, "", cant_tell=True)
        db.session.commit()
        flash("Marked as \"can't tell\". Your manager will see it.", "success")
        return _next_item(user_id, project.id)

    if request.method == "POST":
        answered = 0
        for assignment in current_batch_assignments:
            text = (request.form.get(f"answer_{assignment.id}") or "").strip()
            problem = text and answer_problem(assignment.task.question, text)
            if problem:
                flash(f'"{assignment.task.question.question}": {problem}', "error")
            elif text and _save_answer(assignment, text):
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
