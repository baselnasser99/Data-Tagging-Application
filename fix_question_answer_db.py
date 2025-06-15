#!/usr/bin/env python3
"""
Database migration script to fix QuestionAnswer table structure
This script updates the question_answer table to support multiple answers per user-question pair.
"""

import sqlite3
import os
from datetime import datetime

def migrate_question_answer_table():
    db_path = 'instance/database.db'
    
    if not os.path.exists(db_path):
        print("Database file not found. The new structure will be created when you run the app.")
        return
    
    # Connect to database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("🔄 Starting QuestionAnswer table migration...")
    
    try:
        # Check current table structure
        cursor.execute("PRAGMA table_info(question_answer)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        print(f"📋 Current columns: {column_names}")
        
        # Check if we need to migrate
        if 'id' in column_names:
            print("✅ QuestionAnswer table already has correct structure")
            return
        
        print("📝 Migrating QuestionAnswer table structure...")
        
        # Step 1: Create new table with correct structure
        cursor.execute("""
            CREATE TABLE question_answer_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                answer TEXT NOT NULL,
                submitted_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                task_assignment_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES user (id),
                FOREIGN KEY (question_id) REFERENCES question (id),
                FOREIGN KEY (task_assignment_id) REFERENCES task_assignment (id)
            )
        """)
        print("   ✅ Created new question_answer table structure")
        
        # Step 2: Copy existing data
        cursor.execute("""
            INSERT INTO question_answer_new (user_id, question_id, answer, submitted_date)
            SELECT user_id, question_id, answer, submitted_date 
            FROM question_answer
        """)
        print("   ✅ Copied existing data")
        
        # Step 3: Drop old table
        cursor.execute("DROP TABLE question_answer")
        print("   ✅ Dropped old table")
        
        # Step 4: Rename new table
        cursor.execute("ALTER TABLE question_answer_new RENAME TO question_answer")
        print("   ✅ Renamed new table")
        
        # Commit changes
        conn.commit()
        print("✅ QuestionAnswer table migration completed successfully!")
        
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
        
        # Check table structure
        cursor.execute("PRAGMA table_info(question_answer)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        required_columns = ['id', 'user_id', 'question_id', 'answer', 'submitted_date', 'task_assignment_id']
        for col in required_columns:
            if col in column_names:
                print(f"   ✅ Column {col} exists")
            else:
                print(f"   ❌ Column {col} missing")
                return False
        
        # Check if id is primary key
        cursor.execute("PRAGMA table_info(question_answer)")
        columns = cursor.fetchall()
        id_column = next((col for col in columns if col[1] == 'id'), None)
        if id_column and id_column[5] == 1:  # pk column
            print("   ✅ ID column is primary key")
        else:
            print("   ❌ ID column is not primary key")
            return False
        
        print("✅ Migration verification successful!")
        return True
        
    except Exception as e:
        print(f"❌ Error during verification: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    print("🚀 QuestionAnswer Table Migration")
    print("=" * 50)
    
    migrate_question_answer_table()
    verify_migration()
    
    print("\n" + "=" * 50)
    print("🎯 Migration Complete!")
    print("Users can now answer the same question multiple times for different images.")
    print("Run: python app.py to test the fixes.")
