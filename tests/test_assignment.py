"""Unit tests for the task distribution algorithm (thesis ch. 3 and 6.5.3)."""
from collections import Counter

import pytest

from application.admin import assign_tasks_to_users
from application.models import AnnotationTask, Project, ProjectImage, Question, TaskAssignment


@pytest.fixture
def make_tasks(db):
    def _make(n_images=5, n_questions=2, answers_per_task=1):
        project = Project(name="P", desc="d", is_annotation_project=True, answers_per_task=answers_per_task)
        db.session.add(project)
        db.session.flush()
        questions = [Question(question=f"Q{i}", project_id=project.id) for i in range(n_questions)]
        images = [
            ProjectImage(project_id=project.id, filename=f"{i}.png", original_filename=f"{i}.png", file_path=f"/x/{i}.png")
            for i in range(n_images)
        ]
        db.session.add_all(questions + images)
        db.session.flush()
        tasks = [
            AnnotationTask(project_id=project.id, image_id=img.id, question_id=q.id, required_answers=answers_per_task)
            for img in images
            for q in questions
        ]
        db.session.add_all(tasks)
        db.session.commit()
        return tasks

    return _make


def test_each_task_gets_required_number_of_distinct_users(make_tasks, make_user):
    tasks = make_tasks(n_images=4, n_questions=2, answers_per_task=3)
    users = [make_user() for _ in range(5)]
    created = assign_tasks_to_users(tasks, users, max_questions_per_user=100, answers_per_task=3)
    assert created == len(tasks) * 3
    for task in tasks:
        user_ids = [a.user_id for a in TaskAssignment.query.filter_by(task_id=task.id)]
        assert len(user_ids) == 3
        assert len(set(user_ids)) == 3  # never the same person twice on one task


def test_workload_is_balanced(make_tasks, make_user):
    tasks = make_tasks(n_images=10, n_questions=3, answers_per_task=1)
    users = [make_user() for _ in range(4)]
    assign_tasks_to_users(tasks, users, max_questions_per_user=100, answers_per_task=1)
    load = Counter(a.user_id for a in TaskAssignment.query.all())
    assert sum(load.values()) == 30
    assert max(load.values()) - min(load.values()) <= 1


def test_per_user_cap_is_respected(make_tasks, make_user):
    tasks = make_tasks(n_images=10, n_questions=1, answers_per_task=1)
    users = [make_user() for _ in range(2)]
    created = assign_tasks_to_users(tasks, users, max_questions_per_user=3, answers_per_task=1)
    assert created == 6
    load = Counter(a.user_id for a in TaskAssignment.query.all())
    assert all(v <= 3 for v in load.values())


def test_redundancy_is_limited_by_number_of_users(make_tasks, make_user):
    tasks = make_tasks(n_images=2, n_questions=1, answers_per_task=5)
    users = [make_user() for _ in range(2)]
    assign_tasks_to_users(tasks, users, max_questions_per_user=100, answers_per_task=5)
    for task in tasks:
        assert TaskAssignment.query.filter_by(task_id=task.id).count() == 2


def test_rerunning_does_not_duplicate_assignments(make_tasks, make_user):
    tasks = make_tasks(n_images=3, n_questions=1, answers_per_task=1)
    users = [make_user() for _ in range(2)]
    assign_tasks_to_users(tasks, users, 100, 1)
    assert assign_tasks_to_users(tasks, users, 100, 1) == 0
    assert TaskAssignment.query.count() == 3


def test_no_users_means_no_assignments(make_tasks):
    tasks = make_tasks()
    assert assign_tasks_to_users(tasks, [], 100, 1) == 0
