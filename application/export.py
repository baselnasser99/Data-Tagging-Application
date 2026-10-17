"""Project exports: a labelled dataset ready for analysis or model training.

Two formats share one data model:
  * CSV  - one row per answer; opens in Excel or pandas.
  * JSON - nested by item (image or audio clip; see "media_type"), then
           question, then answers, with per-question
           summaries; the usual input format for machine-learning code.

Agreement (thesis ch. 3.1: inter-annotator agreement as a quality signal)
  * Multiple-choice questions: Fleiss' kappa across all tasks that received
    at least two answers, plus the majority answer and its share per task.
  * Short free-text answers (up to three words): answers are compared after
    normalising case, spacing and punctuation, giving a percent agreement.
  * Longer free text: no score; the answers are listed for a human to read.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .models import AnnotationTask, AnswerType, Project, QuestionAnswer, TaskAssignment

SHORT_TEXT_MAX_WORDS = 3


def normalise(answer: str) -> str:
    text = re.sub(r"[^\w\s]", "", (answer or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def fleiss_kappa(items: list[list[str]]) -> float | None:
    """Fleiss' kappa for items rated by a varying number (>= 2) of raters.

    ``items`` is a list of rating lists, one per task. Returns None when it
    cannot be computed (fewer than one rated item, or a single category so
    chance agreement is 1).
    """
    items = [ratings for ratings in items if len(ratings) >= 2]
    if not items:
        return None
    categories = sorted({r for ratings in items for r in ratings})
    total_ratings = sum(len(r) for r in items)

    p_items = []
    for ratings in items:
        n = len(ratings)
        counts = Counter(ratings)
        p_items.append((sum(c * c for c in counts.values()) - n) / (n * (n - 1)))
    p_bar = sum(p_items) / len(p_items)

    shares = Counter(r for ratings in items for r in ratings)
    p_e = sum((shares[c] / total_ratings) ** 2 for c in categories)
    if p_e >= 1:
        return None
    return round((p_bar - p_e) / (1 - p_e), 3)


def interpret_kappa(kappa: float | None) -> str:
    """Landis and Koch (1977) labels, the scale most papers use."""
    if kappa is None:
        return "not enough data"
    if kappa < 0:
        return "poor"
    for limit, label in ((0.2, "slight"), (0.4, "fair"), (0.6, "moderate"), (0.8, "substantial")):
        if kappa <= limit:
            return label
    return "almost perfect"


def _question_kind(question) -> str:
    if question.answer_type == AnswerType.MCQ:
        return "multiple_choice"
    return "text"


def build_project_export(project: Project) -> dict:
    """Collect every answer of an annotation project into one structure."""
    tasks = (
        AnnotationTask.query.filter_by(project_id=project.id)
        .order_by(AnnotationTask.image_id, AnnotationTask.question_id)
        .all()
    )
    answers = (
        QuestionAnswer.query.join(TaskAssignment, QuestionAnswer.task_assignment_id == TaskAssignment.id)
        .join(AnnotationTask, TaskAssignment.task_id == AnnotationTask.id)
        .filter(AnnotationTask.project_id == project.id)
        .all()
    )
    by_task: dict[int, list] = defaultdict(list)
    for answer in answers:
        assignment = answer.task_assignment
        by_task[assignment.task_id].append(answer)

    images: dict[int, dict] = {}
    per_question_ratings: dict[int, list[list[str]]] = defaultdict(list)

    for task in tasks:
        question = task.question
        kind = _question_kind(question)
        task_answers = sorted(by_task.get(task.id, []), key=lambda a: a.submitted_date or datetime.min)
        values = [a.answer for a in task_answers]

        summary = {"answers_received": len(values), "answers_required": task.required_answers}
        compared = None
        if kind == "multiple_choice":
            compared = values
        elif values and all(len(v.split()) <= SHORT_TEXT_MAX_WORDS for v in values):
            compared = [normalise(v) for v in values]
        if compared:
            ranked = Counter(compared).most_common(2)
            top, count = ranked[0]
            tied = len(ranked) > 1 and ranked[1][1] == count
            # A tie has no majority; None (blank in CSV) instead of an arbitrary first answer.
            summary["majority_answer"] = None if tied else top
            summary["majority_share"] = round(count / len(compared), 3)
            if kind == "multiple_choice":
                per_question_ratings[question.id].append(compared)

        image = images.setdefault(task.image_id, {
            "image_id": task.image_id,
            "file_name": task.image.original_filename,
            "media_type": task.image.media_type or "image",
            "questions": [],
        })
        image["questions"].append({
            "task_id": task.id,
            "question_id": question.id,
            "question": question.question,
            "type": kind,
            "options": [getattr(question, f"option{i}") for i in range(1, (question.number_of_options or 0) + 1)
                        if getattr(question, f"option{i}")] if kind == "multiple_choice" else None,
            "answers": [
                {
                    "annotator": a.user.email,
                    "annotator_name": a.user.full_name,
                    "answer": a.answer,
                    "submitted_at": a.submitted_date.isoformat() if a.submitted_date else None,
                }
                for a in task_answers
            ],
            "summary": summary,
        })

    question_summaries = []
    for question in project.questions:
        kind = _question_kind(question)
        entry = {"question_id": question.id, "question": question.question, "type": kind}
        if kind == "multiple_choice":
            kappa = fleiss_kappa(per_question_ratings.get(question.id, []))
            entry["fleiss_kappa"] = kappa
            entry["agreement"] = interpret_kappa(kappa)
        else:
            entry["agreement"] = "not computed for free text (see per-task majority for short answers)"
        question_summaries.append(entry)

    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "description": project.desc,
            "answers_per_task": project.answers_per_task,
        },
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "images": len(images),
            "tasks": len(tasks),
            "tasks_complete": sum(1 for t in tasks if t.is_completed),
            "answers": len(answers),
        },
        "questions": question_summaries,
        "images": list(images.values()),
    }


CSV_COLUMNS = [
    "image_id", "file_name", "media_type", "question_id", "question", "question_type",
    "annotator", "annotator_name", "answer", "submitted_at", "task_majority_answer", "task_majority_share",
]


def to_csv(data: dict) -> str:
    """One row per answer. Tasks without answers get one row with blank answer fields."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for image in data["images"]:
        for q in image["questions"]:
            base = {
                "image_id": image["image_id"],
                "file_name": image["file_name"],
                "media_type": image["media_type"],
                "question_id": q["question_id"],
                "question": q["question"],
                "question_type": q["type"],
                "task_majority_answer": q["summary"].get("majority_answer", ""),
                "task_majority_share": q["summary"].get("majority_share", ""),
            }
            if not q["answers"]:
                writer.writerow({**base, "annotator": "", "annotator_name": "", "answer": "", "submitted_at": ""})
            for a in q["answers"]:
                writer.writerow({**base, "annotator": a["annotator"], "annotator_name": a["annotator_name"], "answer": a["answer"],
                                 "submitted_at": a["submitted_at"] or ""})
    return out.getvalue()
