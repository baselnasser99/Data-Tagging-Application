import os
import sys

# Add the application directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from application import create_app
from application.extns import db
from application.models import User

def recreate_database():
    """
    Recreate the database with the updated schema
    """
    print("Starting database recreation...")
    
    # Create Flask app context
    app = create_app()
    
    with app.app_context():
        # Get database URI from app config
        db_uri = app.config['SQLALCHEMY_DATABASE_URI']
        print(f"Database URI: {db_uri}")
        
        # Extract the database file path from the URI
        if db_uri.startswith('sqlite:///'):
            db_path = db_uri[10:]  # Remove 'sqlite:///'
            print(f"Database path: {db_path}")
            
            # Check if the database file exists in the current directory
            if os.path.exists(db_path):
                print(f"Removing existing database file: {db_path}")
                os.remove(db_path)
                print("Database file removed.")
            
            # Check if the database file exists in the instance directory
            instance_dir = os.path.join(os.getcwd(), 'instance')
            instance_db_path = os.path.join(instance_dir, 'database.db')
            if os.path.exists(instance_db_path):
                print(f"Removing existing database file in instance directory: {instance_db_path}")
                os.remove(instance_db_path)
                print("Instance database file removed.")
                
            # Create instance directory if it doesn't exist
            if not os.path.exists(instance_dir):
                print(f"Creating instance directory: {instance_dir}")
                os.makedirs(instance_dir)
            
            # Create all tables with the updated schema
            print("Creating new database with updated schema...")
            db.create_all()
            print("Database schema created successfully.")
            
            # Create default admin user
            print("Creating default admin user...")
            admin_full_name = "John Doe"
            admin_email = "admin@email.com"
            admin_password = "123"
            
            admin_user = User.query.filter(User.email == admin_email).first()
            if not admin_user:
                admin_user = User()
                admin_user.register_admin(admin_full_name, admin_email, admin_password)
                print("Default admin user created.")
            else:
                print("Admin user already exists.")
            
            print("Database recreation completed successfully!")
        else:
            print(f"Unsupported database URI: {db_uri}")
            print("This script only supports SQLite databases.")

if __name__ == "__main__":
    recreate_database()