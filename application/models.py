import enum
import secrets

from flask_login import UserMixin
from sqlalchemy import Column, Enum, ForeignKey, Integer, String, Text
from werkzeug.security import check_password_hash, generate_password_hash

from .extns import db


class User(UserMixin, db.Model):
    id = Column(Integer, primary_key=True)
    full_name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False)  # real addresses easily pass 30 characters
    password_hash = Column(String(500), nullable=False)
    role = Column(String(20), nullable=False)  # 'admin', 'project-admin', or 'end-user'
    active = Column(db.Boolean, nullable=False, default=True, server_default=db.true())  # False = removed by an admin; answers kept
    # Part of the id in the session cookie: changing it ends every session, copies included.
    # A random number, not a count: on SQLite a new account can get a deleted account's id, and
    # with a count both would start at 1, so the deleted person's old cookie would open the new account.
    session_version = Column(Integer, nullable=False, default=0, server_default="0")
    # True while they still sign in with a temporary password an admin was shown.
    must_change_password = Column(db.Boolean, nullable=False, default=False, server_default=db.false())

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

    @property
    def is_active(self):  # Flask-Login: removed people cannot sign in
        return self.active

    def get_id(self):  # Flask-Login keeps this in the session cookie; see load_user
        return f"{self.id}:{self.session_version or 0}"

    def end_all_sessions(self):
        """Every session cookie issued so far stops working, wherever it is."""
        old = self.session_version
        while self.session_version == old:
            self.session_version = secrets.randbelow(2**31 - 1) + 1  # fits a signed INT column

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.end_all_sessions()  # whoever knew the old password is signed out

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def _register(self, fullname, email, password, role):
        self.full_name = fullname
        self.email = email
        self.set_password(password)
        self.role = role
        db.session.add(self)

    def register_end_user(self, fullname, email, password):
        self._register(fullname, email, password, "end-user")
        db.session.commit()

    def register_admin(self, fullname, email, password):
        self._register(fullname, email, password, "admin")
        db.session.commit()

    def register_project_admin(self, fullname, email, password, project_id, assigned_by):
        self._register(fullname, email, password, "project-admin")
        db.session.flush()  # Get the user ID
        db.session.add(ProjectAdmin(user_id=self.id, project_id=project_id, assigned_by=assigned_by))
        db.session.commit()

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
    answers_per_task = db.Column(db.Integer, default=1)
    
    # New field for customizing questions per image
    questions_per_image = db.Column(db.Integer, default=5)

    # How work is handed out: False = each (item, question) task on its own (the
    # thesis algorithm); True = whoever gets an item gets all of its questions.
    hand_out_per_item = db.Column(db.Boolean, nullable=False, default=False)

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

    @property
    def options(self):
        """The choices members see: the first ``number_of_options`` columns that aren't blank."""
        shown = (getattr(self, f"option{i}") for i in range(1, min(self.number_of_options or 0, MAX_OPTIONS) + 1))
        return [o.strip() for o in shown if o and o.strip()]


class QuestionAnswer(db.Model):
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("question.id"), nullable=False)
    answer = Column(Text, nullable=False)
    submitted_date = db.Column(db.DateTime, nullable=True, default=db.func.current_timestamp())
    # "Can't tell": the member couldn't answer (a blurry or ambiguous item). It finishes the task like any
    # answer, but agreement, majority and answer counts leave it out, and the results page lists the item.
    cant_tell = Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    # Optional: Link to annotation task for image-based answers
    task_assignment_id = Column(Integer, ForeignKey("task_assignment.id"), nullable=True)
    task_assignment = db.relationship("TaskAssignment")


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
    questions_distributed = db.Column(db.Boolean, default=False)  # still written, but no page reads it

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


# File types accepted inside an annotation project's ZIP upload.
# Form limits. The forms send these counts, and the server loops over them, so they are checked.
MAX_QUESTIONS = 100  # questions in one new project
MAX_OPTIONS = 6  # Question has option1 to option6
MAX_ANSWER_LENGTH = 5000  # characters in one answer
MAX_ANSWERS_PER_TASK = 100  # people asked about one item and question
MAX_TASKS_PER_PERSON = 100_000  # tasks one person may hold in a project


def answer_problem(question, text):
    """What is wrong with storing ``text`` as an answer to ``question``, or None if nothing is.

    The page offers only the question's options, but a form can be sent by hand: an answer that
    is not one of them would count as a separate vote in the agreement score and the majority.
    """
    if len(text) > MAX_ANSWER_LENGTH:
        return f"An answer can be at most {MAX_ANSWER_LENGTH} characters long."
    if question.answer_type == AnswerType.MCQ:
        options = question.options
        if options and text.strip() not in options:
            return "Please choose one of the options."
    return None


def person_problem(full_name, email):
    """Why an account can't have this name and email, or None: the same rule on every form and script."""
    if not full_name or not email:
        return 'Please enter a name and an email address.'
    if len(full_name) > 100 or len(email) > 255 or '@' not in email:  # the column sizes
        return 'Please enter a valid email address and a name of at most 100 characters.'
    return None


def db_now():
    """The database's clock, which stamps every *_date column: compare those with this.

    datetime.now() is the web server's local time; SQLite's clock is UTC, so a
    week counted from datetime.now() is off by the time-zone difference.
    """
    return db.session.scalar(db.select(db.func.current_timestamp()))


def form_int(value, default, low, high):
    """A whole number from a form, kept between low and high; ``default`` if it isn't a number."""
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def option_count(value):
    """A multiple-choice option count from a form, kept to the 2 to 6 the Question columns hold."""
    try:
        return max(2, min(int(value), MAX_OPTIONS))
    except (TypeError, ValueError):
        return 2


IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
AUDIO_EXTENSIONS = ('.mp3', '.wav', '.ogg', '.m4a', '.flac')
# Survey question attachments may also be videos.
VIDEO_EXTENSIONS = ('.mp4', '.webm', '.mov', '.avi', '.wmv')


def is_attachment(filename):
    """Survey question files are served from the app's own address, so only pictures, audio and video:
    an uploaded HTML or SVG file could run scripts as whoever opens it."""
    return filename.lower().endswith(IMAGE_EXTENSIONS + AUDIO_EXTENSIONS + VIDEO_EXTENSIONS)


def media_type_for(filename):
    """Return 'image', 'audio' or None (not accepted) for a file name."""
    name = filename.lower()
    if name.endswith(IMAGE_EXTENSIONS):
        return "image"
    if name.endswith(AUDIO_EXTENSIONS):
        return "audio"
    return None


class ProjectImage(db.Model):
    """One media item (an image or an audio clip) in an annotation project.

    The class keeps its original name so existing databases keep working;
    ``media_type`` says which kind of file it is.
    """
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    media_type = Column(String(10), nullable=False, default="image", server_default="image")
    upload_date = Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    project = db.relationship("Project", back_populates="images")
    annotation_tasks = db.relationship("AnnotationTask", back_populates="image", lazy=True)

    @property
    def is_audio(self):
        return self.media_type == "audio"


class AnnotationTask(db.Model):
    """Individual image-question pairs that need to be answered"""
    # One task per (project, item, question), even if "Hand out tasks" runs twice at the same moment.
    __table_args__ = (db.UniqueConstraint('project_id', 'image_id', 'question_id', name='unique_task_item_question'),)
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


class ExportLog(db.Model):
    """Records every dataset export, for traceability (thesis 6.5.7)."""
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    file_format = Column(String(10), nullable=False)
    answer_count = Column(Integer, nullable=False, default=0)
    created_date = Column(db.DateTime, default=db.func.current_timestamp())

    user = db.relationship("User")


class LoginFailure(db.Model):
    """One wrong sign-in: which address tried, and which account, if it exists (see auth.login).

    Kept in the database (not in memory) so the limits hold across Gunicorn workers and restarts.
    Rows older than the pause are deleted as new ones come in.
    """
    id = Column(Integer, primary_key=True)
    address = Column(String(45), nullable=False, index=True)  # IPv6 needs up to 45 characters
    user_id = Column(Integer, nullable=True, index=True)  # no foreign key: deleting an account keeps working
    # What was typed, in lower case: the pause counts per typed email, so unknown ones pause like real ones.
    email = Column(String(255), nullable=True, index=True)
    at = Column(db.DateTime, nullable=False, index=True)  # UTC


def delete_question_data(question_ids):
    """Delete everything hanging off these questions: answers, tasks, their assignments, survey rows.

    The questions themselves stay; the caller deletes them. One DELETE per table, not one per row:
    a project with thousands of items has tens of thousands of these rows.
    """
    if not question_ids:
        return
    tasks = db.select(AnnotationTask.id).where(AnnotationTask.question_id.in_(question_ids))
    for statement in (
        db.delete(QuestionAnswer).where(QuestionAnswer.question_id.in_(question_ids)),  # task answers too
        db.delete(TaskAssignment).where(TaskAssignment.task_id.in_(tasks)),
        db.delete(AnnotationTask).where(AnnotationTask.question_id.in_(question_ids)),
        db.delete(UserQuestionAssignment).where(UserQuestionAssignment.question_id.in_(question_ids)),
    ):
        db.session.execute(statement)
