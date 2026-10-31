"""Annotation projects: create one, upload its items, hand out tasks, follow
progress and export the dataset. (On screen these are simply "projects".)"""
import io
import json
import logging

from flask import Blueprint, current_app, request, redirect, url_for, flash, render_template, abort, send_file
from flask_login import current_user
from werkzeug.utils import secure_filename

from . import distribution
from . import export as dataset_export
from .extns import db
from .feedback import flash_distribution, warn_idle_members
from .jinja_utils import plural
from .models import (AnswerType, ExportLog, GroupProject, Project, ProjectImage,
                     Question, UserGroup, MAX_ANSWERS_PER_TASK, MAX_QUESTIONS,
                     MAX_TASKS_PER_PERSON, form_int)
from .groups import set_up_new_project, usable_groups
from .permissions import project_admin_access_required, project_admin_required
from .stats import task_counts
from .uploads import save_project_items

audit = logging.getLogger("audit")

annotation_admin = Blueprint("annotation_admin", __name__, url_prefix="/admin")


@annotation_admin.route("/create-annotation-project", methods=["GET", "POST"])
@project_admin_access_required
def create_annotation_project():
    """Create a new image annotation project with customizable questions"""
    if request.method == "POST":
        project_name = request.form.get("project_name")
        project_desc = request.form.get("project_desc")
        max_questions_per_user = form_int(request.form.get("max_questions_per_user"), 100, 1, MAX_TASKS_PER_PERSON)
        answers_per_task = form_int(request.form.get("answers_per_task"), 1, 1, MAX_ANSWERS_PER_TASK)
        total_questions = form_int(request.form.get("total_questions", 0), -1, -1, MAX_QUESTIONS + 1)  # -1: not a number
        assigned_group_id = request.form.get("assigned_group_id")
        if not 0 <= total_questions <= MAX_QUESTIONS:
            flash(f"A project can have at most {MAX_QUESTIONS} questions.", "error")
            return redirect(request.url)

        # Create the annotation project
        project = Project(
            name=project_name,
            desc=project_desc,
            is_annotation_project=True,
            max_questions_per_user=max_questions_per_user,
            answers_per_task=answers_per_task,
            questions_per_image=total_questions, # Set questions_per_image based on total_questions from form
            hand_out_per_item=request.form.get("hand_out") == "item",
        )
        db.session.add(project)
        db.session.flush() # Get the project ID

        set_up_new_project(project, assigned_group_id)

        # Create the questions. Each can be free text or multiple choice
        # (multiple choice makes answers comparable, see the agreement score).
        if total_questions > 0:
            parsed = []
            for i in range(1, total_questions + 1):
                text = (request.form.get(f"question_{i}") or "").strip()
                if not text:
                    continue
                kind = request.form.get(f"question_type_{i}", "text")
                # One box per option; a single box with one option per line is accepted too.
                options = [o.strip() for value in request.form.getlist(f"question_options_{i}")
                           for o in value.splitlines() if o.strip()]
                if kind == "mcq" and not 2 <= len(options) <= 6:
                    flash(f'Question {i} is multiple choice, so it needs between 2 and 6 options.', "error")
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
        flash(f"Project '{project_name}' created successfully!", "success")
        return redirect(url_for("annotation_admin.upload_images", project_id=project.id))

    return render_template("admin/create_annotation_project.html", groups=usable_groups())


@annotation_admin.route("/upload-images/<int:project_id>", methods=["GET", "POST"])
@project_admin_required
def upload_images(project_id):
    """Upload a ZIP of images and/or audio clips for an annotation project."""
    project = db.get_or_404(Project, project_id)

    if not project.is_annotation_project:
        flash("This page is for projects, not surveys.", "error")
        return redirect(url_for("admin.view_projects"))

    if request.method == "POST":
        # One field takes ZIP files and/or loose image and audio files.
        uploads = [f for f in request.files.getlist('image_zip') if f and f.filename]
        if not uploads:
            flash("No file selected!", "error")
            return redirect(request.url)

        result = save_project_items(project_id, uploads)
        if result is None:
            flash("The upload could not be saved, so nothing was added. The error was logged.", "error")
            return redirect(request.url)
        if result.full:
            flash(f"This project has reached its storage limit of {result.quota // (1024 * 1024)} MB, so some files "
                  "were not added. Ask the admin to raise MAX_PROJECT_STORAGE_MB.", "error")
        if result.unreadable:
            flash(f"Could not read: {', '.join(result.unreadable)}.", "warning")
        if result.unsupported:
            flash(f"Skipped files that are not images, audio or ZIP: {', '.join(result.unsupported)}.", "warning")
        if result.duplicates_skipped:
            flash(f"Skipped {plural(result.duplicates_skipped, 'file')} already in this project (same file name).", "warning")
        saved = result.saved
        if not saved["image"] and not saved["audio"]:
            flash("The upload contained no new images or audio files.", "warning")
            return redirect(request.url)
        parts = []
        if saved["image"]:
            parts.append(plural(saved['image'], 'image'))
        if saved["audio"]:
            parts.append(plural(saved['audio'], 'audio file'))
        flash(f"Successfully uploaded {' and '.join(parts)}!", "success")
        return redirect(url_for("annotation_admin.generate_annotation_tasks", project_id=project.id))

    return render_template("admin/upload_images.html", project=project)


@annotation_admin.route("/generate-annotation-tasks/<int:project_id>", methods=["GET", "POST"])
@project_admin_required
def generate_annotation_tasks(project_id):
    """Generate annotation tasks and assign them to users"""
    project = db.get_or_404(Project, project_id)

    if not project.is_annotation_project:
        flash("This page is for projects, not surveys.", "error")
        return redirect(url_for("admin.view_projects"))

    images = ProjectImage.query.filter_by(project_id=project_id).all()
    questions = Question.query.filter_by(project_id=project_id).all()
    groups = usable_groups(project)

    if request.method == "POST":
        selected_group_id = request.form.get("group_id")
        if not selected_group_id:
            flash("Please select a user group!", "error")
            return redirect(request.url)

        group = db.session.get(UserGroup, selected_group_id)
        if not group:
            flash("Selected group not found!", "error")
            return redirect(request.url)
        if group not in groups:
            abort(403)  # the group also works on other managers' projects

        # Record that this group works on the project, so the project shows up
        # on the group's dashboards and in later redistribution.
        if not GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first():
            db.session.add(GroupProject(group_id=group.id, project_id=project.id))
            db.session.commit()

        if not group.members:
            flash("The selected group has no members yet. Tasks were created and will be assigned when people join.", "warning")

        result = distribution.distribute(project)
        flash_distribution(result, f"Tasks are ready for '{project.name}'.")
        warn_idle_members(project, [m.user for m in group.members])
        return redirect(url_for("annotation_admin.view_annotation_project", project_id=project.id))

    assigned_group_ids = {gp.group_id for gp in GroupProject.query.filter_by(project_id=project.id)}
    # Pre-select the biggest group already on the project (one option only, or the browser picks the last).
    on_project = [g for g in groups if g.id in assigned_group_ids]
    default_group_id = max(on_project, key=lambda g: len(g.members)).id if on_project else None
    return render_template("admin/generate_annotation_tasks.html",
                         project=project,
                         images=images,
                         questions=questions,
                         groups=groups,
                         assigned_group_ids=assigned_group_ids,
                         default_group_id=default_group_id)


@annotation_admin.route("/view-annotation-project/<int:project_id>")
@project_admin_required
def view_annotation_project(project_id):
    """View annotation project details and progress"""
    project = db.get_or_404(Project, project_id)

    if not project.is_annotation_project:
        flash("This page is for projects, not surveys.", "error")
        return redirect(url_for("admin.view_projects"))

    images = ProjectImage.query.filter_by(project_id=project_id).all()
    questions = Question.query.filter_by(project_id=project_id).all()
    counts = task_counts(project_id)
    stats = {
        'total_images': len(images),
        'total_audio': sum(1 for i in images if i.is_audio),
        'total_questions': len(questions),
        'completion_percentage': round(counts['completed_tasks'] / counts['total_tasks'] * 100, 1)
                                 if counts['total_tasks'] else 0,
        **counts,
    }

    data = dataset_export.build_project_export(project)
    items = {img.id: img for img in images}
    return render_template("admin/view_annotation_project.html",
                         project=project,
                         stats=stats,
                         results=dataset_export.question_results(data),
                         items=items,
                         workload=distribution.workload_summary(project),
                         exports=ExportLog.query.filter_by(project_id=project.id).order_by(ExportLog.id.desc()).limit(10).all(),
                         images=images[:10],  # Show first 10 images as preview
                         questions=questions)

@annotation_admin.route("/project/<int:project_id>/export.<file_format>")
@project_admin_required
def export_project(project_id, file_format):
    """Download every answer of an annotation project as CSV or JSON."""
    project = db.get_or_404(Project, project_id)
    if file_format not in ("csv", "json"):
        abort(404)
    if not project.is_annotation_project:
        flash("Dataset export is for projects. Use the text download for surveys.", "warning")
        return redirect(url_for("admin.view_projects"))

    data = dataset_export.build_project_export(project)
    db.session.add(ExportLog(project_id=project.id, user_id=current_user.id,
                             file_format=file_format, answer_count=data["totals"]["answers"]))
    db.session.commit()
    current_app.logger.info("EXPORT project=%s format=%s by user=%s", project.id, file_format, current_user.id)
    audit.info("EXPORT project_id=%s format=%s answers=%s by=%s",
               project.id, file_format, data["totals"]["answers"], current_user.id)

    slug = secure_filename(project.name.lower().replace(" ", "-")) or f"project-{project.id}"
    if file_format == "csv":
        body, mimetype = dataset_export.to_csv(data).encode("utf-8"), "text/csv"
    else:
        body, mimetype = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"), "application/json"
    return send_file(io.BytesIO(body), as_attachment=True, download_name=f"{slug}.{file_format}", mimetype=mimetype)
