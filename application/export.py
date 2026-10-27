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
    normalising case, spacing and punctuation, giving a majority per task and,
    per question, a percent agreement (not chance-corrected): the share of
    tasks with two or more answers where everyone gave the same answer.
  * Longer free text: a task with any answer over three words isn't compared,
    so it counts neither for nor against agreement (the results page shows how
    many such tasks there are). Instead the page shows the question's most
    common words, filler words left out, and a few example answers; every
    answer is in the download for a person to read.
  * "Can't tell" answers (cant_tell: true) are exported but left out of all of
    the above: they say nothing about which answer is right.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .models import AnnotationTask, AnswerType, Project, QuestionAnswer, TaskAssignment

SHORT_TEXT_MAX_WORDS = 3
COMMON_WORDS_SHOWN = 8
# Left out of the common words; normalise() has already dropped apostrophes ("it's" -> "its").
FILLER_WORDS = frozenset(
    "a an the and or but of to in on at by for with from as is are was were be been being it its this that "
    "these those there here i im you we they he she his her their our my me some any very just so not no "
    "can could would should will do does did has have had into over under up down out than then also".split()
)


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


def compared_answers(kind: str, values: list[str]) -> list[str] | None:
    """One task's answers as agreement compares them, or None when they aren't compared.

    Multiple choice: as given. Free text: normalised, but only if every answer is short.
    The export's per-task majority and the results page's counts both use this.
    """
    if kind == "multiple_choice":
        return values
    if values and all(len(v.split()) <= SHORT_TEXT_MAX_WORDS for v in values):
        return [normalise(v) for v in values]
    return None


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
    text_agreed: dict[int, list[bool]] = defaultdict(list)  # per comparable free-text task: did everyone agree?

    for task in tasks:
        question = task.question
        kind = _question_kind(question)
        task_answers = sorted(by_task.get(task.id, []), key=lambda a: a.submitted_date or datetime.min)
        values = [a.answer for a in task_answers if not a.cant_tell]  # "can't tell" is no vote

        summary = {"answers_received": len(task_answers), "answers_required": task.required_answers,
                   "cant_tell": len(task_answers) - len(values)}
        compared = compared_answers(kind, values)
        if compared:
            ranked = Counter(compared).most_common(2)
            top, count = ranked[0]
            tied = len(ranked) > 1 and ranked[1][1] == count
            # A tie has no majority; None (blank in CSV) instead of an arbitrary first answer.
            summary["majority_answer"] = None if tied else top
            summary["majority_share"] = round(count / len(compared), 3)
            if kind == "multiple_choice":
                per_question_ratings[question.id].append(compared)
            elif len(compared) >= 2:
                text_agreed[question.id].append(len(set(compared)) == 1)

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
            "options": question.options if kind == "multiple_choice" else None,
            "answers": [
                {
                    "annotator": a.user.email,
                    "annotator_name": a.user.full_name,
                    "answer": a.answer,
                    "cant_tell": bool(a.cant_tell),
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
            agreed = text_agreed.get(question.id, [])
            entry["agreement"] = "percent agreement (not chance-corrected)"
            entry["percent_agreement"] = round(100 * sum(agreed) / len(agreed), 1) if agreed else None
            entry["percent_agreement_tasks"] = len(agreed)
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


def question_results(data: dict, disagreements_shown: int = 6) -> list[dict]:
    """What the answers say, per question, for the Progress and results page.

    Built from build_project_export() so the page and the downloads agree.
    For each question: the agreement (Fleiss' kappa for multiple choice), how
    often each answer was given, how many tasks with two or more answers
    agree completely, and a few tasks where people disagreed. Free text over
    three words isn't compared; its most common words and a few examples are
    shown instead.
    """
    results = []
    for summary in data["questions"]:
        pairs = [(image, q) for image in data["images"] for q in image["questions"]
                 if q["question_id"] == summary["question_id"]]
        tasks = [task for _, task in pairs]
        kind = summary["type"]
        counts: Counter = Counter()
        compared = agreed = not_compared = 0
        disagreements, unclear, long_answers = [], [], []
        for image, task in pairs:
            given = [a for a in task["answers"] if not a["cant_tell"]]
            not_sure = [a for a in task["answers"] if a["cant_tell"]]
            if not_sure and len(unclear) < disagreements_shown:
                unclear.append({"image_id": image["image_id"], "file_name": image["file_name"], "answers": not_sure})
            counts.update(compared_answers(kind, [a["answer"] for a in given]) or [])
            if kind == "text":
                long_answers += [{**a, "file_name": image["file_name"]} for a in given
                                 if len(a["answer"].split()) > SHORT_TEXT_MAX_WORDS]
            if len(given) < 2:
                continue
            if "majority_share" not in task["summary"]:  # long free text: read, not scored
                not_compared += 1
                continue
            compared += 1
            if task["summary"]["majority_share"] == 1:
                agreed += 1
            elif len(disagreements) < disagreements_shown:
                disagreements.append({"image_id": image["image_id"], "file_name": image["file_name"],
                                      "answers": given})
        options = (tasks[0]["options"] if tasks and tasks[0]["options"] else []) if kind == "multiple_choice" else []
        # Choices in the order the question lists them, then anything else people answered.
        order = options + [v for v, _ in counts.most_common() if v not in options]
        top = order if kind == "multiple_choice" else [v for v, _ in counts.most_common(5)]
        total = sum(counts.values())
        # Each word counts once per answer, so one long-winded person can't fill the list.
        words = Counter(w for a in long_answers for w in set(normalise(a["answer"]).split()) if w not in FILLER_WORDS)
        common = sorted(words.items(), key=lambda wc: (-wc[1], wc[0]))[:COMMON_WORDS_SHOWN]
        results.append({
            **summary,
            "answers": sum(len(t["answers"]) for t in tasks),
            "cant_tell": sum(t["summary"]["cant_tell"] for t in tasks),
            "unclear_items": sum(1 for t in tasks if t["summary"]["cant_tell"]),
            "unclear": unclear,
            "distribution": [{"answer": v, "count": counts[v], "share": counts[v] / total if total else 0} for v in top],
            "compared": compared,
            "agreed": agreed,
            "not_compared": not_compared,
            "disagreements": disagreements,
            "long_answers": len(long_answers),
            "common_words": [{"word": w, "count": c, "share": c / len(long_answers)} for w, c in common],
            "examples": long_answers[:3],
        })
    return results


CSV_COLUMNS = [
    "image_id", "file_name", "media_type", "question_id", "question", "question_type",
    "annotator", "annotator_name", "answer", "submitted_at", "task_majority_answer", "task_majority_share",
    "cant_tell",  # added later: last, so files read by column position keep working
]


def csv_cell(value):
    """Stop spreadsheet apps running a cell as a formula (CSV injection): text starting with = + - @ gets a leading '.

    Also after leading spaces: some spreadsheet apps skip them when deciding what a cell is.
    """
    if isinstance(value, str) and (value[:1] in ("\t", "\r") or value.lstrip(" ")[:1] in ("=", "+", "-", "@")):
        return "'" + value
    return value


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
            rows = [{"annotator": a["annotator"], "annotator_name": a["annotator_name"], "answer": a["answer"],
                     "cant_tell": "yes" if a["cant_tell"] else "", "submitted_at": a["submitted_at"] or ""}
                    for a in q["answers"]]
            for row in rows or [{"annotator": "", "annotator_name": "", "answer": "", "cant_tell": "",
                                 "submitted_at": ""}]:
                writer.writerow({k: csv_cell(v) for k, v in {**base, **row}.items()})
    return out.getvalue()
