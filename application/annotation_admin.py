"""Annotation projects: create one, upload its items, hand out tasks, follow
progress and export the dataset. (On screen these are simply "projects".)"""
import io
import json
import logging
import os
import uuid
import zipfile
from collections import Counter

from flask import Blueprint, current_app, request, redirect, url_for, flash, render_template, abort, send_file
from flask_login import current_user
from werkzeug.utils import secure_filename

from . import distribution
from . import export as dataset_export
from .extns import db
from .feedback import flash_distribution, warn_idle_members
from .jinja_utils import plural
from .models import (AnnotationTask, AnswerType, ExportLog, GroupProject, Project, ProjectImage,
                     Question, TaskAssignment, UserGroup, media_type_for)
from .groups import set_up_new_project, usable_groups
from .permissions import project_admin_access_required, project_admin_required
from .uploads import upload_dir

audit = logging.getLogger("audit")

annotation_admin = Blueprint("annotation_admin", __name__, url_prefix="/admin")


@annotation_admin.route("/create-annotation-project", methods=["GET", "POST"])
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
            user_id=current_user.id,
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
                        # A small ZIP can unpack to gigabytes ("ZIP bomb"); zipfile never reads past file_size.
                        limit = current_app.config.get('MAX_UPLOAD_LENGTH')
                        if limit and sum(i.file_size for i in zip_ref.infolist()) > 2 * limit:
                            flash(f"{upload.filename} unpacks to more than {2 * limit // (1024 * 1024)} MB, so it was skipped.", "error")
                            continue
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
            db.session.add(GroupProject(group_id=group.id, project_id=project.id, questions_distributed=True))
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


def _completion(total, completed):
    return {'total': total, 'completed': completed,
            'percentage': round((completed / total * 100) if total else 0, 1)}


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
    tasks = AnnotationTask.query.filter_by(project_id=project_id).all()
    assignments = TaskAssignment.query.join(AnnotationTask).filter(AnnotationTask.project_id == project_id).all()
    
    # Calculate detailed statistics
    total_assignments = len(assignments)
    completed_assignments = len([a for a in assignments if a.is_completed])
    unique_users = {a.user_id for a in assignments}
    total_tasks = len(tasks)

    completed_tasks = sum(1 for task in tasks if task.is_completed)

    # Per-item and per-person completion, counted in one pass over each list
    tasks_per_item = Counter(t.image_id for t in tasks)
    done_per_item = Counter(t.image_id for t in tasks if t.is_completed)
    image_completion = {img.id: _completion(tasks_per_item[img.id], done_per_item[img.id]) for img in images}

    held = Counter(a.user_id for a in assignments)
    answered = Counter(a.user_id for a in assignments if a.is_completed)
    user_assignments = {user_id: _completion(held[user_id], answered[user_id]) for user_id in unique_users}

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
