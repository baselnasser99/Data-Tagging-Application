"""Where uploaded files live, and saving survey question attachments."""
import os
import uuid

from flask import current_app, flash, url_for
from werkzeug.utils import secure_filename

from .models import is_attachment


def upload_dir(*parts):
    """Create (if needed) and return a folder inside the upload root.

    The upload root is ``static/uploads`` unless UPLOAD_ROOT is configured.
    """
    root = current_app.config.get("UPLOAD_ROOT") or os.path.join(current_app.static_folder, "uploads")
    path = os.path.join(root, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def upload_path(filename):
    """Absolute path for a file stored directly in the upload root."""
    return os.path.join(upload_dir(), filename)


def save_attachment(upload):
    """Store a survey question's picture, audio or video and return its stored name (None if nothing was saved)."""
    if not upload or not upload.filename:
        return None
    if not is_attachment(upload.filename):
        flash(f'"{upload.filename}" was not attached: question files must be a picture, audio or video.', 'warning')
        return None
    filename = f"{uuid.uuid4()}{secure_filename(upload.filename)[-64:]}"  # Question.file_path holds 100 characters
    upload.save(upload_path(filename))
    return filename


def public_file_url(filename):
    return url_for("uploaded_file", name=filename, _external=True)
