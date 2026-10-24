"""Survey progress: which assigned questions a member has answered.

The dashboards, a member's project list and leaving a group all ask this; it must give the
same answer everywhere and not cost one database query per assignment.
"""
from contextlib import contextmanager

from sqlalchemy import event

from application.annotation import _my_group_projects
from application.groups import remove_pending_assignments_from_removed_group_member
from application.models import (GroupProject, Project, Question, QuestionAnswer, UserGroup, UserGroupMembership,
                                UserQuestionAssignment)
from application.stats import group_progress, project_progress


def _survey(db, make_user, members=2, questions=2):
    """A survey given to one group: every member holds every question, nobody has answered yet."""
    project = Project(name="Opinions", desc="")
    group = UserGroup(name="Panel")
    db.session.add_all([project, group])
    db.session.commit()
    qs = [Question(question=f"Q{i}", project_id=project.id) for i in range(questions)]
    people = [make_user() for _ in range(members)]
    db.session.add_all(qs)
    db.session.add_all(UserGroupMembership(user_id=p.id, group_id=group.id) for p in people)
    db.session.commit()
    db.session.add(GroupProject(group_id=group.id, project_id=project.id))
    db.session.add_all(UserQuestionAssignment(user_id=p.id, question_id=q.id, group_project_id=f"{group.id}-{project.id}")
                       for p in people for q in qs)
    db.session.commit()
    return project, group, people, qs


def _answer(db, user, question, text="yes"):
    db.session.add(QuestionAnswer(user_id=user.id, question_id=question.id, answer=text))
    db.session.commit()


@contextmanager
def _count_queries(db):
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(db.engine, "before_cursor_execute", record)


def test_progress_counts_answered_assignments(db, make_user):
    project, group, (ada, bob), (q1, q2) = _survey(db, make_user)
    _answer(db, ada, q1)

    assert project_progress(project) == (1, 4)
    assert group_progress(group, [project.id]) == (1, 4)
    by_user = {u.id: _my_group_projects(u.id)[0] for u in (ada, bob)}
    assert (by_user[ada.id]["done"], by_user[ada.id]["total"]) == (1, 2)
    assert (by_user[bob.id]["done"], by_user[bob.id]["total"]) == (0, 2)

    _answer(db, ada, q2)
    assert project_progress(project) == (2, 4)
    assert _my_group_projects(ada.id)[0]["done"] == 2


def test_leaving_a_group_keeps_answered_questions_only(db, make_user):
    project, group, (ada, bob), (q1, q2) = _survey(db, make_user)
    _answer(db, ada, q1)
    UserGroupMembership.query.filter_by(user_id=ada.id, group_id=group.id).delete()
    db.session.commit()

    remove_pending_assignments_from_removed_group_member(ada, group)

    assert {a.question_id for a in UserQuestionAssignment.query.filter_by(user_id=ada.id)} == {q1.id}
    assert UserQuestionAssignment.query.filter_by(user_id=bob.id).count() == 2


def test_progress_queries_do_not_grow_with_assignments(db, make_user):
    """Two members or six: the same number of queries."""
    def queries(members):
        project, group, people, qs = _survey(db, make_user, members=members)
        _answer(db, people[0], qs[0])
        db.session.expire_all()
        with _count_queries(db) as statements:
            project_progress(project)
            group_progress(group, [project.id])
            _my_group_projects(people[0].id)
        for model in (QuestionAnswer, UserQuestionAssignment, UserGroupMembership):
            model.query.delete()
        GroupProject.query.delete()
        Question.query.delete()
        Project.query.delete()
        UserGroup.query.delete()
        db.session.commit()
        return len(statements)

    assert queries(2) == queries(6)
