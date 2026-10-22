'''
Define functions that can be used in html templates
'''

# Get a value of field from object
def getattr_safe(obj, name):
    return getattr(obj, name, '')

def plural(n, word, many=None):
    """plural(1, "image") -> "1 image"; plural(3, "image") -> "3 images"."""
    return f"{n} {word if n == 1 else (many or word + 's')}"


def sidebar_summary(user):
    """The small card at the bottom of the side panel: a few numbers about the signed-in person's work.

    Members see their own tasks; managers their projects this week; admins the whole app this week.
    Returns {"title", "lines": [(label, value)], "done", "total"}; done/total draw a bar when total > 0.
    """
    from datetime import datetime, timedelta

    from .models import AnnotationTask, ProjectAdmin, Question, QuestionAnswer, TaskAssignment

    week_ago = datetime.now() - timedelta(days=7)
    if user.role == "end-user":
        mine = TaskAssignment.query.filter_by(user_id=user.id)
        total, done = mine.count(), mine.filter_by(is_completed=True).count()
        return {"title": "Your progress", "done": done, "total": total,
                "lines": [("Tasks done", f"{done} of {total}"), ("Waiting", total - done)]}

    answers = QuestionAnswer.query.filter(QuestionAnswer.submitted_date >= week_ago)
    open_tasks = TaskAssignment.query.filter(TaskAssignment.is_completed.is_(False))
    if user.role == "project-admin":
        ids = [pa.project_id for pa in ProjectAdmin.query.filter_by(user_id=user.id)]
        answers = answers.join(Question).filter(Question.project_id.in_(ids))
        open_tasks = open_tasks.join(AnnotationTask).filter(AnnotationTask.project_id.in_(ids))
        title = "Your projects this week"
    else:
        title = "This week"
    # Admins and managers count answers: a task (item x question) may need several.
    return {"title": title, "done": 0, "total": 0,
            "lines": [("Answers in 7 days", answers.count()), ("Answers still open", open_tasks.count())]}
