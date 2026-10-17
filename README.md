# 🖼️ Image Annotation & Survey Management System

[![Tests](https://github.com/baselnasser99/Data-Tagging-Application/actions/workflows/tests.yml/badge.svg)](https://github.com/baselnasser99/Data-Tagging-Application/actions/workflows/tests.yml)

A modern web-based platform for managing both image annotation projects and general survey/questionnaire projects, built with Flask. This system allows administrators to create various project types, upload images, define questions (including Multiple Choice), assign tasks to users via groups, and monitor progress. End users can log in, view their assigned tasks, and provide answers efficiently.

---

## Table of Contents

- [Features](#features)
- [User Roles](#user-roles)
- [Admin Perspective](#admin-perspective)
- [Project Admin Perspective](#project-admin-perspective)
- [End User Perspective](#end-user-perspective)
- [Installation & Setup](#installation--setup)
- [Docker Usage](#docker-usage)
- [Security Notes](#security-notes)
- [Support](#support)

---

## Features

- **Flexible Project Creation**:
    - **Survey Projects**: For general questionnaires with open-text or multiple-choice questions.
    - **Annotation Projects**: Label images, audio clips, or a mix of both, with customizable questions per item.
- **Question Management**:
    - Create and edit questions with various answer types (Open Text, Multiple Choice).
    - Define multiple-choice options and correct answers for MCQs.
    - Attach files (images, audio, video) to questions.
- **Batch Upload** (for Annotation Projects): one ZIP with images (PNG, JPG, GIF, BMP, WEBP) and/or audio (MP3, WAV, OGG, M4A, FLAC). Annotators see pictures and hear audio in a built-in player.
- **User Group Management**: Organize users into groups for easier assignment.
- **Project Assignment to Groups**: Assign projects to entire user groups during project creation or later.
- **Smart Task Generation & Assignment**: System intelligently distributes questions/tasks to users based on project settings, group assignments, and completion requirements.
- **Role-Based Access Control**:
    - **System Admin**: Full control over all projects, users, and groups.
    - **Project Admin**: Manage specific projects they are assigned to, including questions, images, and group assignments for that project.
    - **End User**: Access and complete assigned questions/tasks.
- **Progress Tracking**: Monitor real-time statistics on project completion, user progress, and group performance.
- **Data Export**: Download submitted answers for analysis.
- **Intuitive User Interfaces**: Dedicated dashboards and annotation interfaces for different user roles.
- **Secure Authentication**

---

## User Roles

The system supports three distinct user roles:

-   **System Admin**: Has full administrative privileges across the entire application. Can create, edit, and delete any project, manage all users and groups, and assign project admin roles.
-   **Project Admin**: Assigned to specific projects by a System Admin. Can manage only the projects they are assigned to, including editing project details, questions, uploading images, generating tasks, and assigning groups to their projects. They can also create new project admin users for their assigned projects.
-   **End User**: The primary annotators or respondents. They can log in, view projects/tasks assigned to them (via groups), and submit answers.

---

## Admin Perspective (System Admin)

As a **System Admin**, you can:

1.  **Log in to the Admin Dashboard**
    -   There is no default account. The first administrator is created on startup from the `ADMIN_EMAIL` and `ADMIN_PASSWORD` settings in your `.env` file (see [Installation & Setup](#installation--setup)).
2.  **Manage Projects**:
    -   Create new "Survey" or "Image Annotation" projects.
    -   Edit existing projects, including project settings (e.g., questions per image, answers per task for annotation projects).
    -   Manage questions for any project, including adding new questions (Open Text or Multiple Choice), editing existing ones, and deleting them.
    -   Upload ZIP files of images and/or audio clips for annotation projects.
    -   Generate and assign annotation tasks.
    -   View detailed project dashboards and results.
3.  **Manage Users**: View all registered users, their roles, and activity.
4.  **Manage Groups**: Create, edit, and delete user groups. Assign projects to groups during project creation or later.
5.  **Manage Project Admins**: Assign other users the "Project Admin" role for specific projects, granting them limited administrative control.
6.  **Export Data**: Download comprehensive project answers.

---

## Project Admin Perspective

As a **Project Admin**, you can:

1.  **Log in to Your Dashboard**: See an overview of the projects you manage.
2.  **Manage Your Assigned Projects**:
    -   Edit project details and settings.
    -   Add, edit, and delete questions for your projects. This includes defining multiple-choice options and the correct answer for MCQ questions.
    -   Upload images for your annotation projects.
    -   Generate annotation tasks and assign them to user groups with smart distribution.
    -   View project-specific dashboards showing accurate completion percentages based on required answers.
    -   Monitor real-time progress of tasks and user assignments.
3.  **Manage Groups for Your Projects**: Assign user groups to your projects.
4.  **Manage Project-Specific Admins**: Create and remove other Project Admins for the projects you manage.

---

## End User Perspective

As an **End User**, you can:

1.  **Register an Account**
2.  **Log in to Your Dashboard**: View all projects and tasks assigned to you via your groups.
3.  **Complete Tasks**:
    -   Access assigned questions/images.
    -   Provide answers for Open Text questions.
    -   Select options for Multiple Choice Questions.
    -   Submit your responses.
4.  **Track Your Progress**: See how many tasks you have completed and how many remain for each project.
5.  **Update Your Profile**: Change your password or update your information.

---

## Installation & Setup

### Prerequisites

-   Python 3.11+
-   Git
-   Docker (optional, for containerized deployment)

### Configuration

All settings come from environment variables. Copy the example file and edit it:

```bash
cp .env.example .env
```

At minimum set `SECRET_KEY`, `ADMIN_EMAIL` and `ADMIN_PASSWORD`. Without `DATABASE_URL` the app uses a local SQLite file, which is fine for trying it out.

### Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
flask --app app run --debug
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) and sign in with the admin email and password from `.env`.

### Run with Docker (Flask + MySQL 8)

```bash
cp .env.example .env    # then edit the passwords
docker compose up --build
```

The app is served by Gunicorn at [http://localhost:5000](http://localhost:5000). Database data, uploads and logs are kept in Docker volumes.

---

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest --cov
```

The suite covers authentication and access control, CSRF protection, CSV user import, groups, ZIP upload, task generation and distribution, the annotation workflow and exports. GitHub Actions runs it on every push (see the badge at the top).

---

## Security Notes

-   Passwords are hashed with Werkzeug (PBKDF2/scrypt); no plaintext passwords are stored.
-   Sessions use Flask-Login with HTTP-only, SameSite cookies and an idle timeout (`SESSION_IDLE_MINUTES`).
-   Every form is protected against cross-site request forgery (Flask-WTF CSRF tokens).
-   Role-based access control: Admins, Project Admins and End Users have distinct dashboards and permissions.
-   Sign-ins, failed sign-ins and logouts are written to `logs/audit.log`.
-   No secrets or default accounts are stored in the code.

---

## Support

For technical support or questions about the system, contact your system administrator.

---

**Version:** 3.0 (Multi-role & MCQ Support)
**Last Updated:** June 2025