from sqlalchemy import Column, String, Integer, Text, ForeignKey, Enum
import enum
from werkzeug.security import generate_password_hash, check_password_hash
from .extns import db


class User(db.Model):
    id = Column(Integer, primary_key=True)
    full_name = Column(String(50), nullable=False)
    email = Column(String(30), unique=True, nullable=False)
    password_hash = Column(String(500), nullable=False)
    role = Column(String(20), nullable=False)  # 'admin', 'project-admin', or 'end-user'

    question_answers = db.relationship(
        "QuestionAnswer", backref="user", lazy=True)
    
    # Relationships for project admin roles
    project_admin_roles = db.relationship(
        "ProjectAdmin",
        back_populates="user",
        foreign_keys="ProjectAdmin.user_id",
        lazy=True
    )
    assigned_project_admins = db.relationship(
        "ProjectAdmin",
        back_populates="assigner",
        foreign_keys="ProjectAdmin.assigned_by",
        lazy=True
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def register_end_user(self, fullname, email, password):
        self.full_name = fullname
        self.email = email
        self.set_password(password)
        self.role = "end-user"

        db.session.add(self)
        db.session.commit()

    def register_admin(self, fullname, email, password):
        self.full_name = fullname
        self.email = email
        self.set_password(password)
        self.role = "admin"

        db.session.add(self)
        db.session.commit()
        
    def register_project_admin(self, fullname, email, password, project_id, assigned_by):
        self.full_name = fullname
        self.email = email
        self.set_password(password)
        self.role = "project-admin"

        db.session.add(self)
        db.session.flush()  # Get the user ID
        
        # Create the project admin role
        project_admin = ProjectAdmin(
            user_id=self.id,
            project_id=project_id,
            assigned_by=assigned_by
        )
        
        db.session.add(project_admin)
        db.session.commit()

    def is_admin(self):
        return self.role == "admin"

    def is_project_admin(self, project_id=None):
        # If project_id is provided, check if user is a project admin for that specific project
        if project_id:
            return any(pa.project_id == project_id for pa in self.project_admin_roles)
        
        # If no project_id is provided, check if user has the project-admin role
        # or if they are an admin for any project
        return self.role == "project-admin" or len(self.project_admin_roles) > 0


class Project(db.Model):
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    desc = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=True)

    # New fields for image annotation projects
    is_annotation_project = db.Column(db.Boolean, default=False)
    max_questions_per_user = db.Column(db.Integer, default=100)
    answers_per_task = db.Column(db.Integer, default=10)
    
    # New field for customizing questions per image
    questions_per_image = db.Column(db.Integer, default=5)

    questions = db.relationship("Question", backref="belongs_to", lazy=True)

    # Relationships for annotation projects
    images = db.relationship("ProjectImage", back_populates="project", lazy=True)
    annotation_tasks = db.relationship("AnnotationTask", back_populates="project", lazy=True)


class AnswerType(enum.Enum):
    TEXT = "text"
    MCQ = "mcq"


class Question(db.Model):
    id = Column(Integer, primary_key=True)
    question = Column(Text, nullable=False)
    file_path = Column(String(100), nullable=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    answer_type = Column(
        Enum(AnswerType), default=AnswerType.TEXT, nullable=False)
    number_of_options = Column(Integer, default=0, nullable=False)
    option1 = Column(String(50), nullable=True)
    option2 = Column(String(50), nullable=True)
    option3 = Column(String(50), nullable=True)
    option4 = Column(String(50), nullable=True)
    option5 = Column(String(50), nullable=True)
    option6 = Column(String(50), nullable=True)
    answer = Column(Text, nullable=True)

    question_answers = db.relationship(
        "QuestionAnswer", backref="question", lazy=True)


class QuestionAnswer(db.Model):
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("question.id"), nullable=False)
    answer = Column(Text, nullable=False)
    submitted_date = db.Column(db.DateTime, nullable=True, default=db.func.current_timestamp())

    # Optional: Link to annotation task for image-based answers
    task_assignment_id = Column(Integer, ForeignKey("task_assignment.id"), nullable=True)


class UserGroup(db.Model):
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    created_date = Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    members = db.relationship("UserGroupMembership", back_populates="group", cascade="all, delete-orphan")
    assigned_projects = db.relationship("GroupProject", back_populates="group", cascade="all, delete-orphan")


class UserGroupMembership(db.Model):
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('user_group.id'), primary_key=True)
    joined_date = db.Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    user = db.relationship("User", backref="group_memberships")
    group = db.relationship("UserGroup", back_populates="members")


class GroupProject(db.Model):
    group_id = db.Column(db.Integer, db.ForeignKey('user_group.id'), primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), primary_key=True)
    assigned_date = db.Column(db.DateTime, default=db.func.current_timestamp())
    questions_distributed = db.Column(db.Boolean, default=False)

    # Relationships
    group = db.relationship("UserGroup", back_populates="assigned_projects")
    project = db.relationship("Project", backref="assigned_groups")


class UserQuestionAssignment(db.Model):
    """Track which specific questions are assigned to which users from group projects"""
    id = Column(Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    group_project_id = db.Column(db.String(50), nullable=False)  # Reference to group+project combination
    assigned_date = db.Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    user = db.relationship("User", backref="question_assignments")
    question = db.relationship("Question", backref="user_assignments")


# New models for image annotation system
class ProjectImage(db.Model):
    """Stores uploaded images for annotation projects"""
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    upload_date = Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    project = db.relationship("Project", back_populates="images")
    annotation_tasks = db.relationship("AnnotationTask", back_populates="image", lazy=True)


class AnnotationTask(db.Model):
    """Individual image-question pairs that need to be answered"""
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    image_id = Column(Integer, ForeignKey("project_image.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("question.id"), nullable=False)
    created_date = Column(db.DateTime, default=db.func.current_timestamp())

    # How many answers this task needs (e.g., for consensus). Default from project.answers_per_task.
    required_answers = Column(Integer, default=10)
    # Current count of completed TaskAssignments for this AnnotationTask.
    current_answers = Column(Integer, default=0)
    # True if current_answers >= required_answers.
    is_completed = Column(db.Boolean, default=False)

    # Relationships
    project = db.relationship("Project", back_populates="annotation_tasks")
    image = db.relationship("ProjectImage", back_populates="annotation_tasks")
    question = db.relationship("Question", backref="annotation_tasks")
    assignments = db.relationship("TaskAssignment", back_populates="task", lazy=True)


class TaskAssignment(db.Model):
    """Assigns specific annotation tasks to users"""
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("annotation_task.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    assigned_date = Column(db.DateTime, default=db.func.current_timestamp())
    completed_date = Column(db.DateTime, nullable=True)
    is_completed = Column(db.Boolean, default=False)

    # Relationships
    task = db.relationship("AnnotationTask", back_populates="assignments")
    user = db.relationship("User", backref="task_assignments")

    # Unique constraint to prevent duplicate assignments
    __table_args__ = (db.UniqueConstraint('task_id', 'user_id', name='unique_task_user'),)


class ProjectAdmin(db.Model):
    """Tracks which users are admins for which projects"""
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    assigned_by = Column(Integer, ForeignKey("user.id"), nullable=False)  # Who assigned this admin role
    assigned_date = Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    user = db.relationship(
        "User",
        back_populates="project_admin_roles",
        foreign_keys=[user_id]
    )
    assigner = db.relationship(
        "User",
        back_populates="assigned_project_admins",
        foreign_keys=[assigned_by]
    )
    project = db.relationship("Project", backref="project_admins")

    # Ensure a user can only be admin for a project once
    __table_args__ = (db.UniqueConstraint('user_id', 'project_id', name='unique_project_admin'),)
