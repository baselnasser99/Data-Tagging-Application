import os
import sys
import sqlite3

# Add the application directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from application import create_app

def migrate_project_table():
    """
    Add questions_per_image column to the project table
    """
    print("Starting migration to add questions_per_image column to project table...")
    
    # Create Flask app context
    app = create_app()
    
    with app.app_context():
        # Get database URI from app config
        db_uri = app.config['SQLALCHEMY_DATABASE_URI']
        
        # Extract the database file path from the URI
        if db_uri.startswith('sqlite:///'):
            db_path = db_uri[10:]  # Remove 'sqlite:///'
            
            # Check if the database file exists
            if not os.path.exists(db_path):
                print(f"Database file not found at: {db_path}")
                
                # Try to find the database file in the instance directory
                instance_dir = os.path.join(os.getcwd(), 'instance')
                if os.path.exists(instance_dir):
                    print(f"Checking instance directory: {instance_dir}")
                    for file in os.listdir(instance_dir):
                        if file.endswith('.db'):
                            db_path = os.path.join(instance_dir, file)
                            print(f"Found database file: {db_path}")
                            break
                
                if not os.path.exists(db_path):
                    print("No database file found. Listing directories:")
                    print(f"Current directory: {os.getcwd()}")
                    print("Files in current directory:")
                    for file in os.listdir('.'):
                        print(f"- {file}")
                    
                    if os.path.exists('instance'):
                        print("Files in instance directory:")
                        for file in os.listdir('instance'):
                            print(f"- {file}")
                    
                    raise FileNotFoundError(f"Database file not found")
            
            print(f"Using database file: {db_path}")
            
            # Connect to the SQLite database
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            try:
                # Get list of tables
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = cursor.fetchall()
                
                print("Available tables in the database:")
                for table in tables:
                    print(f"- {table[0]}")
                
                # Check if Project table exists
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Project';")
                if not cursor.fetchone():
                    # Try with lowercase
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name)='project';")
                    result = cursor.fetchone()
                    if not result:
                        raise ValueError("Project table not found in the database")
                    project_table_name = result[0]
                else:
                    project_table_name = 'Project'
                
                print(f"Found project table: {project_table_name}")
                
                # Check if the column already exists
                cursor.execute(f"PRAGMA table_info(project);")
                columns = cursor.fetchall()
                column_names = [col[1] for col in columns]
                
                print(f"Columns in project table:")
                for col in columns:
                    print(f"- {col[1]} ({col[2]})")
                
                if 'questions_per_image' not in column_names:
                    print("Adding questions_per_image column...")
                    
                    # Add the column with default value of 5
                    cursor.execute(f"ALTER TABLE project ADD COLUMN questions_per_image INTEGER DEFAULT 5 NOT NULL;")
                    conn.commit()
                    
                    print("Column added successfully!")
                else:
                    print("Column questions_per_image already exists.")
                
                print("Migration completed successfully!")
                
            except Exception as e:
                print(f"Error during migration: {e}")
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            print(f"Unsupported database URI: {db_uri}")
            print("This migration script only supports SQLite databases.")

if __name__ == "__main__":
    migrate_project_table()