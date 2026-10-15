"""Role-based access decorators shared by all blueprints.

Roles (thesis terminology in brackets):
  * ``admin``          - system administrator  [administrator]
  * ``project-admin``  - manages assigned projects only  [manager]
  * ``end-user``       - completes assigned tasks  [annotator]

Logged-out visitors are redirected to the login page with a message.
Logged-in users without the required role get a 403 page.
"""
from functools import wraps

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

from .models import ProjectAdmin


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
    """System admins, or users who manage at least one project."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return _login_redirect()
        if current_user.role == "admin":
            return func(*args, **kwargs)
        if ProjectAdmin.query.filter_by(user_id=current_user.id).first() is None:
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
