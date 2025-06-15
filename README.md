# 🖼️ Image Annotation & Survey Management System

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
    - **Image Annotation Projects**: For image-based tasks with customizable questions per image.
- **Question Management**:
    - Create and edit questions with various answer types (Open Text, Multiple Choice).
    - Define multiple-choice options and correct answers for MCQs.
    - Attach files (images, audio, video) to questions.
- **Batch Image Upload** (for Annotation Projects)
- **User Group Management**: Organize users into groups for easier assignment.
- **Project Assignment to Groups**: Assign projects to entire user groups.
- **Automatic Task Generation & Assignment**: System intelligently distributes questions/tasks to users based on project settings and group assignments.
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
    -   Default credentials:
        -   Email: `admin@email.com`
        -   Password: `123`
    -   **Strongly recommended:** Change these credentials immediately after first login for security.
2.  **Manage Projects**:
    -   Create new "Survey" or "Image Annotation" projects.
    -   Edit existing projects, including project settings (e.g., questions per image, answers per task for annotation projects).
    -   Manage questions for any project, including adding new questions (Open Text or Multiple Choice), editing existing ones, and deleting them.
    -   Upload image ZIP files for annotation projects.
    -   Generate and assign annotation tasks.
    -   View detailed project dashboards and results.
3.  **Manage Users**: View all registered users, their roles, and activity.
4.  **Manage Groups**: Create, edit, and delete user groups. Assign projects to groups.
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
    -   Generate annotation tasks and assign them to user groups.
    -   View project-specific dashboards, progress, and results.
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

-   Python 3.7+
-   pip
-   Git
-   Docker (Optional, for containerized deployment)

### Manual Installation

1.  **Clone the repository**
    ```bash
    git clone <repository-url>
    cd Thesis-Final2
    ```

2.  **Install dependencies**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run database migrations**
    ```bash
    python migrate_db.py
    python migrate_annotation_db.py
    ```
    *Note: If you encounter issues with existing data or schema, you might need to run `python recreate_database.py` (this will delete all existing data).*

4.  **Start the application**
    ```bash
    python app.py
    ```

5.  **Access the application**
    -   Open your browser and go to: [http://127.0.0.1:5000](http://127.0.0.1:5000)

---

## Docker Usage

### Build the Docker Image

```bash
docker build -t annotation-app .
```

### Run the Docker Container

```bash
docker run -d -p 5000:5000 --name annotation-app annotation-app
```

-   The app will be available at: [http://localhost:5000](http://localhost:5000)

### (Optional) Run Database Migrations in Docker

```bash
docker exec -it annotation-app python migrate_db.py
docker exec -it annotation-app python migrate_annotation_db.py
```

---

## Security Notes

-   All passwords are securely hashed.
-   Role-based access control: Admins, Project Admins, and End Users have distinct dashboards and permissions.
-   Change the default admin credentials immediately after first login.

---

## Support

For technical support or questions about the system, contact your system administrator.

---

**Version:** 3.0 (Multi-role & MCQ Support)
**Last Updated:** June 2025