"""Progress and workload figures shown on the admin, manager and group pages."""
from datetime import datetime, timedelta

from .models import AnnotationTask, Question, QuestionAnswer, TaskAssignment, UserQuestionAssignment


def project_progress(project):
    """(done, total) for a whole project: finished tasks, or answered survey questions."""
    if project.is_annotation_project:
        tasks = AnnotationTask.query.filter_by(project_id=project.id)
        return tasks.filter_by(is_completed=True).count(), tasks.count()
    assigned = UserQuestionAssignment.query.join(Question).filter(Question.project_id == project.id).all()
    done = sum(1 for q in assigned if QuestionAnswer.query.filter_by(
        user_id=q.user_id, question_id=q.question_id, task_assignment_id=None).first())
    return done, len(assigned)


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
        UserQuestionAssignment.group_project_id.in_([f"{group.id}-{pid}" for pid in project_ids]))
    total = tasks.count() + survey.count()
    done = tasks.filter(TaskAssignment.is_completed.is_(True)).count() + sum(
        1 for q in survey if QuestionAnswer.query.filter_by(
            user_id=q.user_id, question_id=q.question_id, task_assignment_id=None).first())
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
    now = datetime.now()
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
