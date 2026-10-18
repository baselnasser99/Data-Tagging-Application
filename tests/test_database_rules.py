"""Rules the database itself enforces, so the data stays right even when two requests race."""
import pytest
from sqlalchemy.exc import IntegrityError

from application import _upgrade_schema
from application.models import AnnotationTask, Project, ProjectImage, Question, User


def test_only_one_task_per_item_and_question(app, db):
    project = Project(name="P", desc="d", is_annotation_project=True)
    db.session.add(project)
    db.session.flush()
    question = Question(question="Q?", project_id=project.id)
    image = ProjectImage(project_id=project.id, filename="a.png", original_filename="a.png", file_path="/x/a.png")
    db.session.add_all([question, image])
    db.session.flush()
    for _ in range(2):  # as if "Hand out tasks" were clicked twice at the same moment
        db.session.add(AnnotationTask(project_id=project.id, image_id=image.id, question_id=question.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_names_and_emails_have_room_for_real_addresses():
    assert User.__table__.c.email.type.length == 255
    assert User.__table__.c.full_name.type.length == 100


def test_schema_upgrade_is_safe_to_run_again(app):
    _upgrade_schema(app)  # the rule already exists: nothing to do, no error
    _upgrade_schema(app)
