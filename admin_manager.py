#!/usr/bin/env python3
"""
Admin Management Script for Survey Management System
This script allows you to manage admin accounts easily.
"""

import sys
import os
from getpass import getpass

# Add the application directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from application import create_app
from application.models import User
from application.extns import db

def print_banner():
    print("=" * 60)
    print("📋 Survey Management System - Admin Manager")
    print("=" * 60)
    print()

def list_admins():
    """List all admin users"""
    print("Current Admin Users:")
    print("-" * 40)
    
    admins = User.query.filter_by(role='admin').all()
    if not admins:
        print("❌ No admin users found!")
        return
    
    for i, admin in enumerate(admins, 1):
        print(f"{i}. {admin.full_name} ({admin.email})")
    print()

def change_admin_email():
    """Change admin email"""
    list_admins()
    
    try:
        admin_num = int(input("Enter admin number to update email: ")) - 1
        admins = User.query.filter_by(role='admin').all()
        
        if admin_num < 0 or admin_num >= len(admins):
            print("❌ Invalid admin number!")
            return
        
        admin = admins[admin_num]
        print(f"Current email: {admin.email}")
        
        new_email = input("Enter new email: ").strip()
        if not new_email:
            print("❌ Email cannot be empty!")
            return
        
        # Check if email already exists
        existing = User.query.filter_by(email=new_email).first()
        if existing and existing.id != admin.id:
            print("❌ Email already exists!")
            return
        
        admin.email = new_email
        db.session.commit()
        print(f"✅ Email updated successfully to: {new_email}")
        
    except ValueError:
        print("❌ Please enter a valid number!")
    except Exception as e:
        print(f"❌ Error updating email: {e}")
        db.session.rollback()

def change_admin_password():
    """Change admin password"""
    list_admins()
    
    try:
        admin_num = int(input("Enter admin number to update password: ")) - 1
        admins = User.query.filter_by(role='admin').all()
        
        if admin_num < 0 or admin_num >= len(admins):
            print("❌ Invalid admin number!")
            return
        
        admin = admins[admin_num]
        print(f"Updating password for: {admin.full_name} ({admin.email})")
        
        new_password = getpass("Enter new password: ")
        confirm_password = getpass("Confirm new password: ")
        
        if new_password != confirm_password:
            print("❌ Passwords don't match!")
            return
        
        if len(new_password) < 6:
            print("❌ Password must be at least 6 characters!")
            return
        
        admin.set_password(new_password)
        db.session.commit()
        print("✅ Password updated successfully!")
        
    except ValueError:
        print("❌ Please enter a valid number!")
    except Exception as e:
        print(f"❌ Error updating password: {e}")
        db.session.rollback()

def create_new_admin():
    """Create a new admin user"""
    print("Creating New Admin User")
    print("-" * 30)
    
    try:
        full_name = input("Enter full name: ").strip()
        if not full_name:
            print("❌ Full name cannot be empty!")
            return
        
        email = input("Enter email: ").strip()
        if not email:
            print("❌ Email cannot be empty!")
            return
        
        # Check if email already exists
        existing = User.query.filter_by(email=email).first()
        if existing:
            print("❌ Email already exists!")
            return
        
        password = getpass("Enter password: ")
        confirm_password = getpass("Confirm password: ")
        
        if password != confirm_password:
            print("❌ Passwords don't match!")
            return
        
        if len(password) < 6:
            print("❌ Password must be at least 6 characters!")
            return
        
        # Create new admin
        new_admin = User()
        new_admin.register_admin(
            fullname=full_name,
            email=email,
            password=password
        )
        
        print(f"✅ New admin created successfully!")
        print(f"   Name: {full_name}")
        print(f"   Email: {email}")
        
    except Exception as e:
        print(f"❌ Error creating admin: {e}")
        db.session.rollback()

def main():
    app = create_app()
    
    with app.app_context():
        print_banner()
        
        while True:
            print("Choose an option:")
            print("1. List all admin users")
            print("2. Change admin email")
            print("3. Change admin password")
            print("4. Create new admin user")
            print("5. Exit")
            print()
            
            choice = input("Enter your choice (1-5): ").strip()
            print()
            
            if choice == '1':
                list_admins()
            elif choice == '2':
                change_admin_email()
            elif choice == '3':
                change_admin_password()
            elif choice == '4':
                create_new_admin()
            elif choice == '5':
                print("👋 Goodbye!")
                break
            else:
                print("❌ Invalid choice! Please enter 1-5.")
            
            print("\n" + "=" * 60 + "\n")

if __name__ == "__main__":
    main()
