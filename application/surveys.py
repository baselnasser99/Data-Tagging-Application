"""Handing out survey questions. Every person a survey is given to answers every question.

A person's questions are UserQuestionAssignment rows. Each row has a key saying how the person
got it: "<group id>-<project id>" through a group, "imported-<project id>" from a CSV import.
Annotation projects are handed out by application/distribution.py instead.
"""
from .extns import db
from .models import GroupProject, UserQuestionAssignment


def group_key(group, project):
    return f"{group.id}-{project.id}"


def give_questions(users, project, key, held_under_any_key=False):
    """Give each person every question of the survey that they don't hold yet. Returns how many were added.

    What counts as already held: a question held under ``key``, or with held_under_any_key,
    a question held under any key. Nothing is committed.
    """
    ids, questions = [u.id for u in users], project.questions
    if not ids or not questions:
        return 0
    held = UserQuestionAssignment.query.filter(UserQuestionAssignment.user_id.in_(ids),
                                               UserQuestionAssignment.question_id.in_([q.id for q in questions]))
    if not held_under_any_key:
        held = held.filter_by(group_project_id=key)
    have = {(a.user_id, a.question_id) for a in held}
    added = 0
    for user_id in ids:
        for question in questions:
            if (user_id, question.id) not in have:
                db.session.add(UserQuestionAssignment(user_id=user_id, question_id=question.id, group_project_id=key))
                have.add((user_id, question.id))
                added += 1
    return added


def give_to_new_member(user, group):
    """A new group member gets every question of the group's surveys. Commits."""
    for gp in GroupProject.query.filter_by(group_id=group.id).all():
        if gp.project is not None and not gp.project.is_annotation_project:
            give_questions([user], gp.project, group_key(group, gp.project))
    db.session.commit()


def give_to_group(group, project):
    """Hand a survey out to a group again from the start, e.g. after a question was added or deleted.

    The group's rows for the survey are replaced, so each current member holds each current
    question once. Answers are stored separately, so they are kept. Commits.
    """
    members = [m.user for m in group.members if m.user]
    if not members or not project.questions:
        return
    key = group_key(group, project)
    UserQuestionAssignment.query.filter_by(group_project_id=key).delete()
    db.session.commit()
    give_questions(members, project, key)
    link = GroupProject.query.filter_by(group_id=group.id, project_id=project.id).first()
    if link:
        link.questions_distributed = True  # no page reads it any more
    db.session.commit()
