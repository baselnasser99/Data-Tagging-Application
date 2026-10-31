"""Where uploaded files live, saving a project's items, and saving survey question attachments."""
import logging
import os
import uuid
import zipfile
from dataclasses import dataclass, field

from flask import current_app, flash, url_for
from werkzeug.utils import secure_filename

from .extns import db
from .models import ProjectImage, is_attachment, media_type_for

log = logging.getLogger(__name__)


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


class _ProjectFull(Exception):
    """The project's storage limit was reached while a file was being copied."""


def _folder_size(path):
    return sum(entry.stat().st_size for entry in os.scandir(path) if entry.is_file())


def stored_name(original_filename):
    """The file name to store an item under (after a uuid): safe, short, and with its extension.

    secure_filename drops letters outside ASCII, so "фото.png" used to become just "png": a file
    without an extension, served as a download instead of an image.
    """
    stem, extension = os.path.splitext(original_filename)
    return f"{(secure_filename(stem) or 'item')[:100]}{extension.lower()}"


@dataclass
class ItemUpload:
    """What saving a project's items did, for the page to report."""
    quota: int = 0
    saved: dict = field(default_factory=lambda: {"image": 0, "audio": 0})
    duplicates_skipped: int = 0
    unsupported: list = field(default_factory=list)
    unreadable: list = field(default_factory=list)
    full: bool = False


def save_project_items(project_id, uploads):
    """Store images and audio clips, loose or in ZIPs, as a project's items and commit their rows.

    Returns an ItemUpload, or None if the rows could not be committed; the files are removed again then.
    A ZIP that is invalid or unpacks too large is skipped with a message.
    """
    upload_dir_path = upload_dir('images', str(project_id))
    existing_names = {
        img.original_filename
        for img in ProjectImage.query.filter_by(project_id=project_id).all()
    }
    # One manager shouldn't be able to fill the disk every project shares.
    result = ItemUpload(quota=current_app.config.get('PROJECT_UPLOAD_QUOTA') or 0)
    room = result.quota - _folder_size(upload_dir_path) if result.quota else None
    written = []  # files this request saved: removed again if it fails

    def save(original_filename, src):
        """Store one item unless its name is already in the project, copying it in chunks within the room left."""
        nonlocal room
        if original_filename in existing_names:
            result.duplicates_skipped += 1
            return
        media_type = media_type_for(original_filename)
        unique_filename = f"{uuid.uuid4()}_{stored_name(original_filename)}"
        final_path = os.path.join(upload_dir_path, unique_filename)
        size = 0
        try:
            with open(final_path, 'wb') as dst:
                while chunk := src.read(1024 * 1024):  # never a whole (perhaps 1 GB) entry in memory
                    size += len(chunk)
                    if room is not None and size > room:
                        raise _ProjectFull
                    dst.write(chunk)
        except Exception as error:  # full, or an entry zipfile can't read (encrypted, unknown compression)
            if os.path.exists(final_path):
                os.remove(final_path)
            if isinstance(error, _ProjectFull):
                result.full = True
            else:
                log.warning("Upload to project %s: could not store %r: %s", project_id, original_filename, error)
                result.unreadable.append(original_filename)
            return
        written.append(final_path)
        if room is not None:
            room -= size
        db.session.add(ProjectImage(
            project_id=project_id,
            filename=unique_filename,
            original_filename=original_filename[:255],
            file_path=final_path,
            media_type=media_type,
        ))
        existing_names.add(original_filename)
        result.saved[media_type] += 1

    for upload in uploads:
        if result.full:
            break
        if upload.filename.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(upload, 'r') as zip_ref:
                    # A small ZIP can unpack to gigabytes ("ZIP bomb"); zipfile never reads past file_size.
                    limit = current_app.config.get('MAX_UPLOAD_LENGTH')
                    unpacked = sum(i.file_size for i in zip_ref.infolist())
                    if limit and unpacked > 2 * limit:
                        flash(f"{upload.filename} unpacks to more than {2 * limit // (1024 * 1024)} MB, so it was skipped.", "error")
                        continue
                    if room is not None and unpacked > room:
                        flash(f"{upload.filename} unpacks to more than the {room // (1024 * 1024)} MB left in "
                              "this project's storage limit, so it was skipped.", "error")
                        continue
                    for file_info in zip_ref.infolist():
                        original_filename = os.path.basename(file_info.filename)
                        # Nested folders are flattened; junk and unsupported files are ignored.
                        if (file_info.is_dir()
                                or file_info.filename.startswith('__MACOSX/')
                                or original_filename.startswith('.')
                                or media_type_for(original_filename) is None):
                            continue
                        if result.full:
                            break
                        with zip_ref.open(file_info) as src:
                            save(original_filename, src)
            except zipfile.BadZipFile:
                flash(f"{upload.filename} is not a valid ZIP archive, so it was skipped.", "error")
        else:
            original_filename = os.path.basename(upload.filename)
            if media_type_for(original_filename) is None or original_filename.startswith('.'):
                result.unsupported.append(original_filename)
                continue
            save(original_filename, upload)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        for path in written:  # the rows are gone, so the files must go too
            os.remove(path)
        log.exception("Upload to project %s could not be saved", project_id)
        return None
    return result


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
