# Project Documentation

## Overview
This is a web-based project management system that allows administrators to create and manage projects, assign questions to users through groups, and collect answers. The system supports both regular survey projects and annotation projects for image-based tasks.

## System Architecture

### Technology Stack
- Backend: Python with Flask framework
- Database: SQLAlchemy ORM with SQLite/PostgreSQL
- Frontend: HTML, CSS, JavaScript with Bootstrap framework
- Authentication: Session-based authentication system
- File Storage: Local file system for question attachments and images
- Task Queue: Background task processing for large operations

### System Workflows

1. **Project Creation Workflow**
   ```
   Admin/Project Admin
   ├── Create Project
   │   ├── Set Project Type (Survey/Annotation)
   │   ├── Configure Project Settings
   │   ├── Set Required Answers per Task
   │   ├── Define Questions
   │   └── Assign to Groups
   │       ├── Select Target Groups
   │       └── Configure Distribution Rules
   ├── Generate Tasks
   │   ├── Create Annotation Tasks
   │   └── Assign Users with Smart Distribution
   └── Monitor Progress
       ├── Track Completion (Based on Required Answers)
       ├── View User Progress
       └── Generate Reports
   ```

2. **Question Distribution Workflow**
   ```
   System
   ├── Group Assignment
   │   ├── Calculate Distribution
   │   ├── Create Assignments
   │   └── Notify Users
   ├── User Access
   │   ├── View Assigned Questions
   │   ├── Submit Answers
   │   └── Track Progress
   └── Admin Monitoring
       ├── View Statistics
       ├── Manage Assignments
       └── Generate Reports
   ```

3. **Group Management Workflow**
   ```
   Admin
   ├── Create Group
   │   ├── Add Members
   │   └── Set Permissions
   ├── Assign Projects
   │   ├── Select Projects
   │   └── Configure Access
   └── Monitor Activity
       ├── Track Progress
       └── Manage Members
   ```

### Implementation Details

1. **Database Relationships**
   ```
   User
   ├── UserGroupMembership
   │   └── UserGroup
   │       └── GroupProject
   │           └── Project
   │               └── Question
   │                   └── QuestionAnswer
   └── UserQuestionAssignment
       └── Question
   ```

2. **File Structure**
   ```
   application/
   ├── models/
   │   ├── user.py
   │   ├── project.py
   │   └── question.py
   ├── routes/
   │   ├── admin.py
   │   ├── user.py
   │   └── api.py
   ├── templates/
   │   ├── admin/
   │   └── user/
   └── static/
       ├── css/
       ├── js/
       └── uploads/
   ```

3. **API Endpoints**
   ```
   /api/
   ├── /projects
   │   ├── GET /list
   │   ├── POST /create
   │   └── PUT /update
   ├── /questions
   │   ├── GET /list
   │   ├── POST /create
   │   └── PUT /update
   └── /groups
       ├── GET /list
       ├── POST /create
       └── PUT /update
   ```

### Use Cases

1. **Survey Project Creation**
   ```
   Scenario: Admin creates a new survey project
   Steps:
   1. Admin navigates to project creation page
   2. Selects "Survey Project" type
   3. Configures project settings
   4. Adds questions (text/MCQ)
   5. Assigns project to groups
   6. System distributes questions
   ```

2. **Annotation Project Workflow**
   ```
   Scenario: Image annotation project
   Steps:
   1. Admin creates annotation project
   2. Uploads images
   3. Defines annotation questions
   4. Assigns to user groups
   5. System creates tasks
   6. Users annotate images
   ```

3. **Group Management**
   ```
   Scenario: Adding users to group
   Steps:
   1. Admin selects group
   2. Adds new members
   3. System assigns projects
   4. Users receive notifications
   5. System tracks progress
   ```

### Key Components

1. **User Management**
   - User roles: Admin, Project Admin, End User
   - User authentication and authorization
   - User group management for organizing users
   - User import/export functionality
   - User activity tracking and statistics

2. **Project Management**
   - Two types of projects:
     - Regular Survey Projects
     - Annotation Projects (for image-based tasks)
   - Project settings and configuration
   - Question management within projects
   - Project progress tracking
   - Project statistics and reporting

3. **Question Management**
   - Support for multiple question types:
     - Text-based questions
     - Multiple Choice Questions (MCQ)
   - File attachments for questions
   - Question distribution system
   - Question validation rules
   - Question dependency management

4. **Group Management**
   - User group creation and management
   - Project assignment to groups
   - Question distribution within groups
   - Group performance metrics
   - Group activity monitoring

## Detailed Functionality

### 1. Question Distribution System

The system implements a sophisticated task distribution mechanism to ensure efficient and fair distribution of tasks among users:

#### Regular Projects
- Questions are distributed evenly among group members
- Distribution algorithm:
  ```python
  questions_per_user = total_questions // total_members
  remaining_questions = total_questions % total_members
  ```
- Each user gets a fair share of questions

#### Annotation Projects
- Tasks are generated with one-to-one mapping of questions to images
- Smart distribution based on:
  - Required answers per task
  - Current answers received
  - User capacity (max_questions_per_user)
  - Group assignments
- Completion tracking based on required answers
- Real-time progress updates for administrators
- Questions are assigned one-by-one to users
- System tracks assigned questions through `UserQuestionAssignment` model
- Features:
  - Batch assignment capability
  - Progress tracking per user
  - Reassignment of unanswered questions
  - Question rotation for fairness
  - Priority-based distribution

#### Annotation Projects
- Questions are distributed in batches (5 at a time by default)
- Smart distribution algorithm that:
  - Prevents duplicate assignments
  - Respects maximum questions per user limit
  - Ensures even distribution of image annotation tasks
  - Implements round-robin assignment
  - Handles task dependencies
- System tracks task assignments through `TaskAssignment` model
- Features:
  - Image-based task grouping
  - Multiple answers per task support
  - Quality control mechanisms
  - Task validation rules
  - Progress monitoring

### 2. Safe Question Type Editing

The system implements safety measures when editing question types:

- When changing a question type (e.g., from text to MCQ):
  - System checks for existing answers
  - If answers exist:
    - Admin is warned about potential data loss
    - Change is blocked unless answers are explicitly deleted
    - Provides option to export existing answers
    - Shows impact analysis of the change
  - If no answers exist:
    - Question type can be changed freely
    - System validates new question format
    - Updates related metadata
- MCQ-specific features:
  - Dynamic number of options (2-6)
  - Option text management
  - Correct answer selection
  - Option randomization
  - Option validation rules
- Safety features:
  - Change history tracking
  - Rollback capability
  - Validation before save
  - Impact analysis
  - User notification system

### 3. Group Assignment Management

The system handles user group changes efficiently:

#### Adding Users to Groups
- When a user is added to a group:
  - System automatically assigns all active questions from group's projects
  - For regular projects:
    - Questions are assigned through `UserQuestionAssignment`
    - System checks for existing assignments
    - Handles assignment conflicts
    - Updates user statistics
  - For annotation projects:
    - Tasks are assigned through `TaskAssignment`
    - System respects project's `max_questions_per_user` limit
    - Implements smart task distribution
    - Handles task dependencies
- Features:
  - Bulk user addition
  - Assignment validation
  - Progress tracking
  - Notification system
  - Assignment history

#### Removing Users from Groups
- When a user is removed from a group:
  - System removes pending (unanswered) assignments
  - Completed answers are preserved
  - For regular projects:
    - Pending `UserQuestionAssignment` records are deleted
    - Handles answer preservation
    - Updates statistics
  - For annotation projects:
    - Pending `TaskAssignment` records are deleted
    - Completed answers remain in the database
    - Updates task distribution
- Features:
  - Bulk removal support
  - Data preservation rules
  - Assignment cleanup
  - History tracking
  - Impact analysis

## Database Schema

### Key Models

1. **User**
   - Basic user information
   - Role management
   - Group memberships
   - Activity tracking
   - Performance metrics

2. **UserGroup**
   - Group information
   - Member management
   - Project assignments
   - Group statistics
   - Activity history

3. **Project**
   - Project details
   - Question management
   - Group assignments
   - Progress tracking
   - Performance metrics

4. **Question**
   - Question content
   - Question type
   - MCQ options
   - File attachments
   - Validation rules

5. **UserQuestionAssignment**
   - Tracks question assignments to users
   - Links to group projects
   - Assignment dates
   - Progress tracking
   - Status management

6. **QuestionAnswer**
   - Stores user answers
   - Links to questions and users
   - Submission timestamps
   - Validation status
   - Quality metrics

## Security Features

1. **Authentication**
   - Session-based authentication
   - Role-based access control
   - Secure password handling
   - Session management
   - Login attempt tracking

2. **Authorization**
   - Project-level access control
   - Group-based permissions
   - Admin privileges management
   - Resource access control
   - Operation validation

3. **Data Protection**
   - Input validation
   - SQL injection prevention
   - XSS protection
   - CSRF protection
   - Data encryption

## Best Practices

1. **Code Organization**
   - Modular structure
   - Clear separation of concerns
   - Reusable components
   - Documentation standards
   - Code review process

2. **Error Handling**
   - Comprehensive error messages
   - Graceful failure handling
   - User-friendly notifications
   - Error logging
   - Recovery procedures

3. **Performance**
   - Efficient database queries
   - Optimized question distribution
   - Batch processing for large datasets
   - Caching strategies
   - Resource optimization

## Deployment

The system can be deployed using:
- Docker containerization
- Traditional web server setup
- Cloud platform deployment

### Docker Deployment
- Multi-container setup
- Volume management
- Network configuration
- Environment variables
- Health monitoring

### Traditional Deployment
- Web server configuration
- Database setup
- File system management
- Security hardening
- Backup procedures

### Cloud Deployment
- Container orchestration
- Auto-scaling
- Load balancing
- Monitoring
- Disaster recovery

## Monitoring and Maintenance

1. **System Monitoring**
   - Performance metrics
   - Error tracking
   - User activity
   - Resource usage
   - Health checks

2. **Backup and Recovery**
   - Database backups
   - File system backups
   - Recovery procedures
   - Data retention
   - Archive management

3. **Updates and Maintenance**
   - Version control
   - Update procedures
   - Migration scripts
   - Testing procedures
   - Rollback plans

## User Interface

1. **Admin Interface**
   - Dashboard
   - Project management
   - User management
   - Group management
   - Reporting tools

2. **End User Interface**
   - Project view
   - Question answering
   - Progress tracking
   - Profile management
   - Help system

3. **Mobile Responsiveness**
   - Responsive design
   - Touch optimization
   - Performance optimization
   - Offline capabilities
   - Progressive enhancement

## Error Handling and Recovery

1. **Common Error Scenarios**
   ```
   - Database connection failures
   - File upload errors
   - Assignment conflicts
   - Permission issues
   - Validation failures
   ```

2. **Recovery Procedures**
   ```
   - Automatic retry mechanisms
   - Manual intervention steps
   - Data recovery processes
   - System restoration
   - User notification
   ```

3. **Logging and Monitoring**
   ```
   - Error logging
   - Performance monitoring
   - User activity tracking
   - System health checks
   - Alert mechanisms
   ```

## Performance Optimization

1. **Database Optimization**
   ```
   - Index optimization
   - Query optimization
   - Connection pooling
   - Caching strategies
   - Batch processing
   ```

2. **Application Optimization**
   ```
   - Code optimization
   - Resource management
   - Load balancing
   - Caching implementation
   - Async processing
   ```

3. **User Experience**
   ```
   - Response time optimization
   - UI/UX improvements
   - Mobile optimization
   - Offline capabilities
   - Progressive loading
   ``` 