from flask import Blueprint, render_template, redirect, request, url_for, flash, session
from .extns import db
from .models import User, ProjectAdmin
from werkzeug.security import check_password_hash, generate_password_hash

auth = Blueprint("auth", __name__)

def is_project_admin(project_id):
    """Helper function to check if current user is admin for a specific project"""
    if not session.get('id'):
        return False
    
    user = User.query.get(session['id'])
    if not user:
        return False
        
    return user.is_project_admin(project_id)


@auth.route("/", methods=['GET', 'POST'])
@auth.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password_hash, password):
            flash("Email address or password is incorrect!", "danger")
            return redirect(url_for("auth.login"))

        session['id'] = user.id
        session['role'] = user.role
        session['name'] = user.full_name
        session['email'] = user.email

        if user.role == 'admin':
            return redirect(url_for("admin.dashboard"))
        elif user.role == 'project-admin':
            return redirect(url_for("admin.my_projects"))
        elif user.role == 'end-user':
            return redirect(url_for('user.dashboard'))

        return redirect(url_for("auth.login"))
    
    if session:
        role = session.get('role')
        if role == 'admin':
            return redirect(url_for("admin.dashboard"))
        elif role == 'project-admin':
            return redirect(url_for("admin.my_projects"))
        elif role == 'end-user':
            return redirect(url_for('user.dashboard'))
    return render_template("login.html")


@auth.route("/register", methods=['GET', 'POST'])
def register():

    if request.method == 'POST':
        fullname = request.form.get("fullname")
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter(User.email == email).first()

        if user:
            flash("Email Address already exist!", "danger")
            return redirect(url_for("auth.register"))

        User().register_end_user(fullname, email, password)

        flash("Account Created Successfully!", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@auth.route("/logout")
def logout():
    if 'id' in session:
        session.pop('id')
        session.pop('role')
        session.pop('name', None)
        session.pop('email', None)
        flash('Logged out!', 'warning')

    return redirect(url_for('auth.login'))


@auth.route("/test-admin")
def test_admin():
    """Test route to check if admin user exists"""
    admin_user = User.query.filter_by(email="admin@email.com").first()
    if admin_user:
        return f"Admin user exists: {admin_user.full_name}, Role: {admin_user.role}"
    else:
        return "Admin user not found"
