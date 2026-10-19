"""Role-based access decorators shared by all blueprints.

Roles (thesis terminology in brackets):
  * ``admin``          - system administrator  [administrator]
  * ``project-admin``  - manages assigned projects only  [manager]
  * ``end-user``       - completes assigned tasks  [annotator]

Logged-out visitors are redirected to the login page with a message.
Logged-in users without the required role get a 403 page.
"""
import posixpath
from functools import wraps

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user



def _login_redirect():
    flash("Please log in to continue.", "warning")
    return redirect(url_for("auth.login", next=request.full_path))


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return _login_redirect()
        if current_user.role != "admin":
            abort(403)
        return func(*args, **kwargs)

    return wrapper


def project_admin_required(func):
    """System admins, or project admins of the ``project_id`` in the URL."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return _login_redirect()
        if current_user.role == "admin":
            return func(*args, **kwargs)
        project_id = kwargs.get("project_id")
        if not project_id or not current_user.is_project_admin(project_id):
            abort(403)
        return func(*args, **kwargs)

    return wrapper


def project_admin_access_required(func):
    """System admins, and managers (also those who don't run a project yet)."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return _login_redirect()
        if current_user.role == "admin":
            return func(*args, **kwargs)
        if not current_user.is_project_admin():  # the manager role, or managing a project
            abort(403)
        return func(*args, **kwargs)

    return wrapper


def user_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return _login_redirect()
        if current_user.role != "end-user":
            abort(403)
        return func(*args, **kwargs)

    return wrapper


def is_project_admin(project_id):
    return current_user.is_authenticated and current_user.is_project_admin(project_id)


def project_for_upload(name):
    """The id of the project an uploaded file belongs to, or None if it belongs to none.

    Project items are stored as ``images/<project id>/<file>``; survey question
    attachments sit in the upload root and are named in ``Question.file_path``.
    """
    from .models import Question

    parts = posixpath.normpath(name).split("/")  # "images/1/../2/x" is project 2's file
    if len(parts) == 3 and parts[0] == "images" and parts[1].isdigit():
        return int(parts[1])
    if len(parts) == 1:
        question = Question.query.filter_by(file_path=parts[0]).first()
        return question.project_id if question else None
    return None


def can_see_project(user, project_id):
    """Admins, the project's managers, and members of a group assigned to it."""
    from .models import GroupProject, UserGroupMembership

    if user.role == "admin" or user.is_project_admin(project_id):
        return True
    return (GroupProject.query
            .join(UserGroupMembership, UserGroupMembership.group_id == GroupProject.group_id)
            .filter(GroupProject.project_id == project_id, UserGroupMembership.user_id == user.id)
            .first() is not None)


def can_open_upload(name):
    """May the signed-in person open this uploaded file?"""
    if current_user.role == "admin":
        return True
    project_id = project_for_upload(name)
    return project_id is not None and can_see_project(current_user, project_id)
