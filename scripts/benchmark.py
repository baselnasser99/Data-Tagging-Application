#!/usr/bin/env python3
"""Measurements for the evaluation chapter: speed, fairness and a small load test.

Everything runs on a throwaway database filled with made-up people and items,
using the same distribution code the app uses (application/distribution.py).
Nothing touches your real data.

    python scripts/benchmark.py                 # 1k, 10k and 50k tasks
    python scripts/benchmark.py --sizes 1000    # one size only
    python scripts/benchmark.py --load          # also: 20 people answering at once

The database is a temporary SQLite file unless BENCH_DATABASE_URL is set
(GitHub Actions sets it to a MySQL 8 server, like the real deployment).
Results are printed as Markdown tables, and also written to the GitHub job
summary when GITHUB_STEP_SUMMARY is set.
"""
import argparse
import http.cookiejar
import os
import random
import statistics
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo root

from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm import joinedload  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from application import create_app, distribution  # noqa: E402
from application.config import TestingConfig  # noqa: E402
from application.extns import db  # noqa: E402
from application.models import (AnnotationTask, GroupProject, Project, ProjectImage, Question,  # noqa: E402
                                TaskAssignment, User, UserGroup, UserGroupMembership)

QUESTIONS = 10
SCENARIOS = {  # tasks -> (people, items); every item gets QUESTIONS questions
    1_000: (20, 100),
    10_000: (50, 1_000),
    50_000: (100, 5_000),
}
PASSWORD = "bench-password"


def make_app():
    url = os.environ.get("BENCH_DATABASE_URL") or "sqlite:///" + os.path.join(tempfile.mkdtemp(), "bench.db")

    class BenchConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = url
        SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    return create_app(BenchConfig)


def reset_database():
    db.session.remove()
    db.drop_all()
    db.create_all()


def build(people, items, questions=QUESTIONS, answers_per_task=1, password_hash="x"):
    """A group of `people` working on one project of `items` x `questions`."""
    group = UserGroup(name="Bench group")
    project = Project(name="Bench project", desc="made-up data", is_annotation_project=True,
                      answers_per_task=answers_per_task, max_questions_per_user=1_000_000)
    db.session.add_all([group, project])
    db.session.flush()
    users = [User(full_name=f"Person {i:04d}", email=f"bench{i:04d}@example.com", role="end-user",
                  password_hash=password_hash) for i in range(people)]
    db.session.add_all(users)
    db.session.add_all([Question(question=f"Question {q + 1}?", project_id=project.id) for q in range(questions)])
    db.session.add_all([ProjectImage(project_id=project.id, filename=f"{i}.png", original_filename=f"{i}.png",
                                     file_path=f"/bench/{i}.png") for i in range(items)])
    db.session.add(GroupProject(group_id=group.id, project_id=project.id))
    db.session.flush()
    db.session.add_all([UserGroupMembership(user_id=u.id, group_id=group.id) for u in users])
    db.session.commit()
    return project, group, users


def loads(project):
    """(open, total) tasks per person still in the project."""
    rows = distribution.workload_summary(project)
    return [r["open"] for r in rows], [r["total"] for r in rows]


def gap(values):
    return max(values) - min(values) if values else 0


def every_task_owned(project):
    """Every task has as many people as it needs, and nobody holds the same task twice."""
    tasks = AnnotationTask.query.filter_by(project_id=project.id).count()
    per_task = (db.session.query(func.count(TaskAssignment.id), func.count(func.distinct(TaskAssignment.user_id)))
                .join(AnnotationTask).filter(AnnotationTask.project_id == project.id)
                .group_by(TaskAssignment.task_id).all())
    return len(per_task) == tasks and all(n == project.answers_per_task == people for n, people in per_task)


def finish_half(project, rng):
    """Pretend half of the handed-out tasks have been answered, so leaving members have finished work too."""
    assignments = (TaskAssignment.query.join(AnnotationTask).filter(AnnotationTask.project_id == project.id)
                   .options(joinedload(TaskAssignment.task)).all())
    for a in rng.sample(assignments, len(assignments) // 2):
        a.is_completed = True
        a.task.current_answers += 1
        a.task.is_completed = a.task.current_answers >= a.task.required_answers
    db.session.commit()


def run_scenario(tasks, rng):
    people, items = SCENARIOS[tasks]
    reset_database()
    project, group, users = build(people, items)

    start = time.perf_counter()
    distribution.distribute(project)
    handout = time.perf_counter() - start
    _, total = loads(project)
    owned_after_handout = every_task_owned(project)

    finish_half(project, rng)
    done_before = TaskAssignment.query.filter_by(is_completed=True).count()
    open_before_leave, _ = loads(project)  # people finish at different speeds, so this is already uneven

    leavers = rng.sample(users, max(1, people // 10))  # 10% leave
    start = time.perf_counter()
    for user in leavers:
        UserGroupMembership.query.filter_by(user_id=user.id, group_id=group.id).delete()
        db.session.commit()
        distribution.on_member_removed(user, group.id)
    leave = time.perf_counter() - start
    open_after_leave, _ = loads(project)
    kept = TaskAssignment.query.filter_by(is_completed=True).count() == done_before

    joiners = [User(full_name=f"Newcomer {i}", email=f"new{i:04d}@example.com", role="end-user", password_hash="x")
               for i in range(max(1, people // 20))]  # 5% join
    db.session.add_all(joiners)
    db.session.commit()
    start = time.perf_counter()
    for user in joiners:
        db.session.add(UserGroupMembership(user_id=user.id, group_id=group.id))
        db.session.commit()
        distribution.on_member_added(user, group.id)
    join = time.perf_counter() - start
    open_after_join, _ = loads(project)

    return {
        "tasks": tasks, "people": people, "handout_s": handout, "gap_total": gap(total),
        "gap_open_start": gap(open_before_leave),
        "leavers": len(leavers), "leave_s": leave, "gap_open_leave": gap(open_after_leave),
        "joiners": len(joiners), "join_s": join, "gap_open_join": gap(open_after_join),
        "owned": owned_after_handout and every_task_owned(project), "kept": kept,
    }


def load_test(app, people=20, items=50, questions=2):
    """`people` members sign in at the same time and answer all their tasks; times every request."""
    reset_database()
    project, _, users = build(people, items, questions, password_hash=generate_password_hash(PASSWORD))
    distribution.distribute(project)
    work = {u.email: [a.id for a in TaskAssignment.query.filter_by(user_id=u.id)] for u in users}
    db.session.remove()

    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    timings, errors, lock = [], [], threading.Lock()

    def request(opener, path, data=None):
        start = time.perf_counter()
        try:
            body = urllib.parse.urlencode(data).encode() if data else None
            with opener.open(base + path, data=body, timeout=30) as resp:
                resp.read()
                ok = resp.status < 400
        except Exception as exc:  # timeouts and HTTP errors both count as failures
            ok = False
            with lock:
                errors.append(f"{path}: {exc}")
        with lock:
            timings.append(time.perf_counter() - start)
        return ok

    def member(email, assignment_ids):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        request(opener, "/login", {"email": email, "password": PASSWORD})
        request(opener, "/user/annotation-tasks")
        for assignment_id in assignment_ids:
            request(opener, f"/user/annotation-task/{assignment_id}")
            request(opener, f"/user/annotation-task/{assignment_id}", {"answer": "a label"})

    threads = [threading.Thread(target=member, args=item) for item in work.items()]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start
    server.shutdown()

    with app.app_context():
        answered = TaskAssignment.query.filter_by(is_completed=True).count()
        total = TaskAssignment.query.count()
    ordered = sorted(timings)
    return {
        "people": people, "requests": len(timings), "errors": len(errors), "seconds": elapsed,
        "per_second": len(timings) / elapsed, "median_ms": statistics.median(ordered) * 1000,
        "p95_ms": ordered[int(len(ordered) * 0.95) - 1] * 1000, "max_ms": ordered[-1] * 1000,
        "answered": answered, "total": total, "first_errors": errors[:3],
    }


def report(rows, load, database):
    lines = [f"## Hand-out speed and fairness ({database})", "",
             "| Tasks | People | Hand out | Gap after hand-out | Open gap, half answered | 10% leave | Open gap after | 5% join | Open gap after | Every task owned | Finished work kept |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|:-:|:-:|"]
    for r in rows:
        lines.append(f"| {r['tasks']:,} | {r['people']} | {r['handout_s']:.2f} s | {r['gap_total']} | {r['gap_open_start']} "
                     f"| {r['leavers']} in {r['leave_s']:.2f} s | {r['gap_open_leave']} "
                     f"| {r['joiners']} in {r['join_s']:.2f} s | {r['gap_open_join']} "
                     f"| {'yes' if r['owned'] else 'NO'} | {'yes' if r['kept'] else 'NO'} |")
    lines += ["", "Gap = most tasks held by one person minus fewest. 'Open' counts unfinished tasks only: "
                  "half the work is marked as answered at random, as if people worked at different speeds, "
                  "so the open gap is uneven before anyone leaves."]
    if load:
        lines += ["", f"## Load test ({database})", "",
                  "| People at once | Requests | Errors | Time | Requests/s | Median | 95th percentile | Slowest | Tasks answered |",
                  "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
                  f"| {load['people']} | {load['requests']} | {load['errors']} | {load['seconds']:.1f} s "
                  f"| {load['per_second']:.0f} | {load['median_ms']:.0f} ms | {load['p95_ms']:.0f} ms "
                  f"| {load['max_ms']:.0f} ms | {load['answered']} of {load['total']} |"]
        if load["first_errors"]:
            lines += ["", "First errors: " + "; ".join(load["first_errors"])]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="*", default=list(SCENARIOS), choices=list(SCENARIOS))
    parser.add_argument("--load", action="store_true", help="also run the load test")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)  # the same made-up data every run
    app = make_app()
    database = app.config["SQLALCHEMY_DATABASE_URI"].split(":", 1)[0].split("+")[0]
    with app.app_context():
        rows = []
        for tasks in args.sizes:
            print(f"Running {tasks:,} tasks...", file=sys.stderr, flush=True)
            rows.append(run_scenario(tasks, rng))
        load = None
        if args.load:
            print("Running the load test...", file=sys.stderr, flush=True)
            load = load_test(app)
        db.session.remove()
        db.drop_all()

    text = report(rows, load, database)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(text + "\n")
    return rows, load


if __name__ == "__main__":
    main()
