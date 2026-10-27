"""Progress and workload figures shown on the admin, manager and group pages."""
from datetime import timedelta

from .extns import db
from .models import AnnotationTask, Question, QuestionAnswer, TaskAssignment, UserQuestionAssignment, db_now


def answered(assignments):
    """The (user id, question id) pairs among these survey assignments that have an answer.

    One query however many assignments there are. Survey answers have no task assignment.
    """
    users, questions = {a.user_id for a in assignments}, {a.question_id for a in assignments}
    if not users:
        return set()
    rows = db.session.query(QuestionAnswer.user_id, QuestionAnswer.question_id).filter(
        QuestionAnswer.user_id.in_(users), QuestionAnswer.question_id.in_(questions),
        QuestionAnswer.task_assignment_id.is_(None)).distinct()
    return {(user_id, question_id) for user_id, question_id in rows}


def count_answered(assignments):
    """How many of these survey assignments have been answered."""
    done = answered(assignments)
    return sum(1 for a in assignments if (a.user_id, a.question_id) in done)


def project_progress(project):
    """(done, total) for a whole project: finished tasks, or answered survey questions."""
    if project.is_annotation_project:
        tasks = AnnotationTask.query.filter_by(project_id=project.id)
        return tasks.filter_by(is_completed=True).count(), tasks.count()
    assigned = UserQuestionAssignment.query.join(Question).filter(Question.project_id == project.id).all()
    return count_answered(assigned), len(assigned)


def answer_totals(projects, progress):
    """(answers given, answers needed) across these projects, for the dashboards' "Answers given".

    A project's tasks need answers_per_task answers each (one per assignment);
    a survey's questions one answer per member. ``progress`` is project_progress per project id.
    """
    held = TaskAssignment.query.join(AnnotationTask).filter(
        AnnotationTask.project_id.in_([p.id for p in projects if p.is_annotation_project]))
    surveys = [p for p in projects if not p.is_annotation_project]
    return (held.filter(TaskAssignment.is_completed.is_(True)).count() + sum(progress[p.id][0] for p in surveys),
            held.count() + sum(progress[p.id][1] for p in surveys))


def group_progress(group, project_ids):
    """(done, total) of the work the group's members hold in these projects.

    Annotation work counts finished task assignments; survey work counts
    answered question assignments.
    """
    member_ids = [m.user_id for m in group.members]
    tasks = TaskAssignment.query.join(AnnotationTask).filter(
        TaskAssignment.user_id.in_(member_ids), AnnotationTask.project_id.in_(project_ids))
    survey = UserQuestionAssignment.query.filter(
        UserQuestionAssignment.user_id.in_(member_ids),
        UserQuestionAssignment.group_project_id.in_([f"{group.id}-{pid}" for pid in project_ids])).all()
    total = tasks.count() + len(survey)
    done = tasks.filter(TaskAssignment.is_completed.is_(True)).count() + count_answered(survey)
    return done, total


def workload_balance(project):
    """Open and finished tasks per member of the project's groups, most still open first.

    A person's share is every task they hold, open or done: that is what the
    hand-out keeps even (application/distribution.py). Open counts drift apart
    as people answer at different speeds.
    """
    members = {m.user for gp in project.assigned_groups for m in gp.group.members if m.user.active}
    counts = {u.id: {'name': u.full_name, 'open': 0, 'done': 0} for u in members}
    held = TaskAssignment.query.join(AnnotationTask).filter(
        AnnotationTask.project_id == project.id, TaskAssignment.user_id.in_(counts))
    for a in held:
        counts[a.user_id]['done' if a.is_completed else 'open'] += 1
    people = sorted(counts.values(), key=lambda p: (-p['open'], -p['done'], p['name']))
    shares = [p['open'] + p['done'] for p in people]
    return {'project': project, 'people': people, 'open': sum(p['open'] for p in people),
            'share_min': min(shares, default=0), 'share_max': max(shares, default=0)}


def weekly_answers(project_ids=None):
    """Answers submitted per week over the last 12 weeks, oldest first; only these projects if given."""
    now = db_now()
    weeks = []
    for i in range(11, -1, -1):
        start, end = now - timedelta(weeks=i + 1), now - timedelta(weeks=i)
        query = QuestionAnswer.query.filter(QuestionAnswer.submitted_date >= start, QuestionAnswer.submitted_date < end)
        if project_ids is not None:
            query = query.join(Question).filter(Question.project_id.in_(project_ids))
        weeks.append({'week': f"{start.day} {start:%b}",  # the week's first day, e.g. "3 Aug"
                      'answers': query.count()})
    return weeks


def group_stats_for(groups, project_ids=None):
    """Each group's share of finished work, counting only these projects if given.

    Annotation work counts finished task assignments, survey work answered questions.
    """
    stats = []
    for group in groups:
        ids = [gp.project_id for gp in group.assigned_projects
               if project_ids is None or gp.project_id in project_ids]
        if project_ids is not None and not ids:
            continue
        done, total = group_progress(group, ids)
        stats.append({'name': group.name, 'members': len(group.members), 'projects': len(ids),
                      'completion_rate': round(done / total * 100, 1) if total else 0})
    return stats
