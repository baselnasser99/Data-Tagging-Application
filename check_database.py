import os
import sys
import sqlite3

# Add the application directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from application import create_app

def check_database():
    """
    Check the database structure
    """
    print("Checking database structure...")
    
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
                
                # Check the instance directory
                instance_dir = os.path.join(os.getcwd(), 'instance')
                if os.path.exists(instance_dir):
                    print(f"Checking instance directory: {instance_dir}")
                    for file in os.listdir(instance_dir):
                        if file.endswith('.db'):
                            db_path = os.path.join(instance_dir, file)
                            print(f"Found database file: {db_path}")
                            break
                
                # Check if we found a database file
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
                    
                    return
            
            print(f"Using database file: {db_path}")
            
            # Connect to the SQLite database
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            try:
                # Get list of tables
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = cursor.fetchall()
                
                print("Tables in the database:")
                for table in tables:
                    print(f"- {table[0]}")
                    
                    # Get columns for each table
                    cursor.execute(f"PRAGMA table_info({table[0]});")
                    columns = cursor.fetchall()
                    print(f"  Columns in {table[0]} table:")
                    for col in columns:
                        print(f"  - {col[1]} ({col[2]})")
                    print()
                
            except Exception as e:
                print(f"Error checking database: {e}")
            finally:
                conn.close()
        else:
            print(f"Unsupported database URI: {db_uri}")
            print("This script only supports SQLite databases.")

if __name__ == "__main__":
    check_database()