import pymysql
pymysql.install_as_MySQLdb()

import os
from flask import Flask
from application.models import *
from .admin import admin
from .auth import auth
from .end_user import end_user
from .extns import db
from .jinja_utils import getattr_safe


def create_app():
    app = Flask(__name__)

    app.config['SECRET_KEY'] = "thisisasecretkey"
    # Use SQLite for local development, MySQL for production
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
        'DATABASE_URL',
        'sqlite:///database.db'
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    app.register_blueprint(auth)
    app.register_blueprint(admin)
    app.register_blueprint(end_user)
    
    #Set jinja utils to use    
    app.jinja_env.globals['getattr_safe'] = getattr_safe

    db.init_app(app)
    with app.app_context():
        db.create_all()

        admin_full_name = "John Doe"
        admin_email = "admin@email.com"
        admin_password = "123"

        admin_user = User.query.filter(User.email == admin_email).first()
        if not admin_user:
            User().register_admin(admin_full_name, admin_email, admin_password)

    return app
