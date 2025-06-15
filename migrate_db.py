#!/usr/bin/env python3
"""
Database migration script to add submitted_date column to question_answer table
and remove UserProject table references.
"""

import sqlite3
import os
from datetime import datetime
from application import create_app
from application.extns import db
from application.models import User, ProjectAdmin
from sqlalchemy import text

def migrate_database():
    db_path = 'instance/database.db'
    
    if not os.path.exists(db_path):
        print("Database file not found. Creating new database...")
        return
    
    # Connect to database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if submitted_date column exists
        cursor.execute("PRAGMA table_info(question_answer)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'submitted_date' not in columns:
            print("Adding submitted_date column to question_answer table...")
            # Add column without default first
            cursor.execute("""
                ALTER TABLE question_answer
                ADD COLUMN submitted_date DATETIME
            """)

            # Update existing records with current timestamp
            cursor.execute("""
                UPDATE question_answer
                SET submitted_date = CURRENT_TIMESTAMP
                WHERE submitted_date IS NULL
            """)
            print("✓ Added submitted_date column")
        else:
            print("✓ submitted_date column already exists")
        
        # Check if user_project table exists and drop it
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='user_project'
        """)
        
        if cursor.fetchone():
            print("Dropping user_project table (individual assignments removed)...")
            cursor.execute("DROP TABLE user_project")
            print("✓ Dropped user_project table")
        else:
            print("✓ user_project table already removed")
        
        # Commit changes
        conn.commit()
        print("\n✅ Database migration completed successfully!")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_database()

app = create_app()

with app.app_context():
    # Create ProjectAdmin table
    db.create_all()

    # Update existing users to have proper roles
    with db.engine.connect() as conn:
        # Update admin user role if it exists
        conn.execute(
            text("UPDATE user SET role = 'admin' WHERE email = 'admin@email.com'")
        )
        conn.commit()

    print("Database migration completed successfully!")
