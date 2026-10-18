"""The demo-data script builds a complete, consistent demo and refuses to touch a database in use."""
import importlib.util
import pathlib

import pytest

from application.models import Project, QuestionAnswer, TaskAssignment, User, UserGroup

spec = importlib.util.spec_from_file_location(
    "seed_demo", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "seed_demo.py")
seed_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed_demo)


def test_seed_demo_fills_an_empty_database(app, make_user):
    make_user(role="admin", email="admin@example.com")
    seed_demo.main(app)
    assert User.query.filter_by(role="end-user").count() == 12
    assert User.query.filter_by(role="project-admin").count() == 1
    assert UserGroup.query.count() == 3 and Project.query.count() == 2
    assert TaskAssignment.query.count() == 96 and QuestionAnswer.query.count() == 64
    first = Project.query.filter_by(name="Road sign shapes").one().images[0]
    with open(first.file_path, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"


def test_seed_demo_refuses_a_database_with_people(app, make_user):
    make_user(role="admin", email="admin@example.com")
    make_user()
    with pytest.raises(SystemExit):
        seed_demo.main(app)
