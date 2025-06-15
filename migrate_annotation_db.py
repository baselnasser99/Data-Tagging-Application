#!/usr/bin/env python3
"""
Database migration script for Image Annotation System
This script adds the new tables and columns needed for the annotation features.
"""

import sqlite3
import os
from datetime import datetime

def migrate_database():
    db_path = 'instance/database.db'
    
    if not os.path.exists(db_path):
        print("Database file not found. The new tables will be created when you run the app.")
        return
    
    # Connect to database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("🔄 Starting database migration for Image Annotation System...")
    
    try:
        # Add new columns to Project table
        print("📝 Adding annotation fields to Project table...")
        try:
            cursor.execute("ALTER TABLE project ADD COLUMN is_annotation_project BOOLEAN DEFAULT 0")
            print("   ✅ Added is_annotation_project column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print("   ⚠️  is_annotation_project column already exists")
            else:
                raise
        
        try:
            cursor.execute("ALTER TABLE project ADD COLUMN max_questions_per_user INTEGER DEFAULT 100")
            print("   ✅ Added max_questions_per_user column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print("   ⚠️  max_questions_per_user column already exists")
            else:
                raise
        
        try:
            cursor.execute("ALTER TABLE project ADD COLUMN answers_per_task INTEGER DEFAULT 10")
            print("   ✅ Added answers_per_task column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print("   ⚠️  answers_per_task column already exists")
            else:
                raise
        
        # Create ProjectImage table
        print("📝 Creating ProjectImage table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_image (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                filename VARCHAR(255) NOT NULL,
                original_filename VARCHAR(255) NOT NULL,
                file_path VARCHAR(500) NOT NULL,
                upload_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES project (id)
            )
        """)
        print("   ✅ ProjectImage table created")
        
        # Create AnnotationTask table
        print("📝 Creating AnnotationTask table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS annotation_task (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                image_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                required_answers INTEGER DEFAULT 10,
                current_answers INTEGER DEFAULT 0,
                is_completed BOOLEAN DEFAULT 0,
                FOREIGN KEY (project_id) REFERENCES project (id),
                FOREIGN KEY (image_id) REFERENCES project_image (id),
                FOREIGN KEY (question_id) REFERENCES question (id)
            )
        """)
        print("   ✅ AnnotationTask table created")
        
        # Create TaskAssignment table
        print("📝 Creating TaskAssignment table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_assignment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                assigned_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_date DATETIME,
                is_completed BOOLEAN DEFAULT 0,
                FOREIGN KEY (task_id) REFERENCES annotation_task (id),
                FOREIGN KEY (user_id) REFERENCES user (id),
                UNIQUE(task_id, user_id)
            )
        """)
        print("   ✅ TaskAssignment table created")
        
        # Create upload directories
        print("📝 Creating upload directories...")
        upload_dir = os.path.join('application', 'static', 'uploads', 'images')
        os.makedirs(upload_dir, exist_ok=True)
        print(f"   ✅ Created directory: {upload_dir}")
        
        # Commit changes
        conn.commit()
        print("✅ Database migration completed successfully!")
        
        # Show summary
        print("\n📊 Migration Summary:")
        print("   • Added annotation fields to Project table")
        print("   • Created ProjectImage table for image storage")
        print("   • Created AnnotationTask table for image-question pairs")
        print("   • Created TaskAssignment table for user assignments")
        print("   • Created upload directories for images")
        print("\n🎉 Your system is now ready for Image Annotation projects!")
        
    except Exception as e:
        print(f"❌ Error during migration: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def verify_migration():
    """Verify that the migration was successful"""
    db_path = 'instance/database.db'
    
    if not os.path.exists(db_path):
        print("❌ Database file not found")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        print("\n🔍 Verifying migration...")
        
        # Check if new columns exist in project table
        cursor.execute("PRAGMA table_info(project)")
        columns = [column[1] for column in cursor.fetchall()]
        
        required_columns = ['is_annotation_project', 'max_questions_per_user', 'answers_per_task']
        for col in required_columns:
            if col in columns:
                print(f"   ✅ Project.{col} exists")
            else:
                print(f"   ❌ Project.{col} missing")
                return False
        
        # Check if new tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [table[0] for table in cursor.fetchall()]
        
        required_tables = ['project_image', 'annotation_task', 'task_assignment']
        for table in required_tables:
            if table in tables:
                print(f"   ✅ Table {table} exists")
            else:
                print(f"   ❌ Table {table} missing")
                return False
        
        print("✅ Migration verification successful!")
        return True
        
    except Exception as e:
        print(f"❌ Error during verification: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    print("🚀 Image Annotation System Database Migration")
    print("=" * 50)
    
    migrate_database()
    verify_migration()
    
    print("\n" + "=" * 50)
    print("🎯 Next Steps:")
    print("1. Run: python app.py")
    print("2. Login as admin")
    print("3. Go to 'Create Annotation Project'")
    print("4. Upload images and assign to groups")
    print("5. Users can start annotating!")
