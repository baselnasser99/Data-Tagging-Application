#!/usr/bin/env python3
"""Compare three ways of handing out annotation work under the same attrition scenario.

    python scripts/simulate_allocation.py                  # 5,000 tasks, 50 people, r = 1 and 2, 20 seeds
    python scripts/simulate_allocation.py --seeds 3 --r 1  # quicker

The scenario (the same for every model with the same seed):
  * every annotator has a fixed, skewed work speed (tasks per round, lognormal),
  * work happens in rounds; after 40% of all answers are given, 10% of the
    annotators leave, and after 60%, new annotators join,
  * r answers are needed per task, never two from the same person.

The models:
  static     tasks are split evenly at the start and never move. A leaver's
             unfinished tasks stay stranded; newcomers get nothing.
  self       self-selection: everyone takes the next free task when ready and
             holds one round's worth of claimed tasks. Claims of someone who
             leaves are released only after a timeout of 5 rounds.
  app        the real application/distribution.py (distribute,
             on_member_removed, on_member_added) on an in-memory SQLite
             database, built with the app factory and TestingConfig.

Every measurement is reported as mean (min-max) over the seeds.
"""
import argparse
import heapq
import math
import os
import random
import statistics
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo root

from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm import joinedload  # noqa: E402

from application import create_app, distribution  # noqa: E402
from application.config import TestingConfig  # noqa: E402
from application.extns import db  # noqa: E402
from application.models import (AnnotationTask, GroupProject, Project, ProjectImage, Question,  # noqa: E402
                                TaskAssignment, User, UserGroup, UserGroupMembership)

LEAVE_AT, JOIN_AT = 0.4, 0.6  # share of all answers given before people leave / join
TIMEOUT = 5  # rounds before a leaver's claimed tasks are free again (self-selection)
MAX_ROUNDS = 10_000


@dataclass
class Scenario:
    seed: int
    tasks: int
    people: int
    r: int
    speeds: list  # tasks per round for annotators 0 .. people + joiners - 1
    leavers: list  # indices of the people who leave
    joiners: list  # indices of the people who join


def make_scenario(seed, tasks=5_000, people=50, r=1, joiners=5):
    rng = random.Random(seed)
    # Lognormal: most people answer 1-3 tasks a round, a few answer 8 or more.
    speeds = [max(1, round(rng.lognormvariate(math.log(2), 0.9))) for _ in range(people + joiners)]
    leavers = sorted(rng.sample(range(people), max(1, people // 10)))
    return Scenario(seed, tasks, people, r, speeds, leavers, list(range(people, people + joiners)))


# ---- the three models -------------------------------------------------------------
# Each model answers the same questions: work(round) gives this round's answers and
# returns how many; depart/join apply the events; open_loads() is unfinished work per
# active annotator; stranded() is unfinished work held by people who left.

class Static:
    """Even split at the start; nothing ever moves."""

    def __init__(self, sc):
        self.sc, self.active = sc, set(range(sc.people))
        n = sc.people
        self.todo = {a: [] for a in range(sc.people + len(sc.joiners))}
        for t in range(sc.tasks):
            for k in range(sc.r):  # task t's r slots go to r different people
                self.todo[(t * sc.r + k) % n].append(t)
        self.answers = {a: 0 for a in self.todo}
        self.given = {t: [] for t in range(sc.tasks)}

    def work(self, _round):
        done = 0
        for a in self.active:
            batch, self.todo[a] = self.todo[a][:self.sc.speeds[a]], self.todo[a][self.sc.speeds[a]:]
            for t in batch:
                self.given[t].append(a)
            self.answers[a] += len(batch)
            done += len(batch)
        return done

    def depart(self, leavers, _round):
        self.active -= set(leavers)

    def join(self, joiners, _round):
        self.active |= set(joiners)  # they get nothing

    def tick(self, _round):
        pass

    def pending(self):
        return False

    def open_loads(self):
        return [len(self.todo[a]) for a in self.active]

    def stranded(self, leavers):
        return sum(len(self.todo[a]) for a in leavers)


class SelfSelection:
    """Everyone takes the next free task when ready, holding one round's worth of claims."""

    def __init__(self, sc):
        self.sc, self.active = sc, set(range(sc.people))
        everyone = range(sc.people + len(sc.joiners))
        self.free = {t: sc.r for t in range(sc.tasks)}  # open answer slots per task
        self.heap = list(range(sc.tasks))  # tasks that may still have a free slot, lowest id first
        self.touched = {t: set() for t in range(sc.tasks)}  # people who claimed or answered it
        self.claims = {a: [] for a in everyone}
        self.answers = {a: 0 for a in everyone}
        self.given = {t: [] for t in range(sc.tasks)}
        self.orphans = []  # (release round, task, leaver)
        for a in sorted(self.active):
            self.claim(a)

    def claim(self, a):
        skipped = []
        while len(self.claims[a]) < self.sc.speeds[a] and self.heap:
            t = heapq.heappop(self.heap)
            if self.free[t] == 0:
                continue
            if a in self.touched[t]:
                skipped.append(t)  # never the same task twice; leave it for someone else
                continue
            self.free[t] -= 1
            self.touched[t].add(a)
            self.claims[a].append(t)
            if self.free[t]:
                heapq.heappush(self.heap, t)
        for t in skipped:
            heapq.heappush(self.heap, t)

    def work(self, _round):
        done = 0
        for a in sorted(self.active):
            for t in self.claims[a]:
                self.given[t].append(a)
            self.answers[a] += len(self.claims[a])
            done += len(self.claims[a])
            self.claims[a] = []
            self.claim(a)  # ready again: take the next free tasks
        return done

    def depart(self, leavers, round_):
        for a in leavers:
            self.active.discard(a)
            self.orphans += [(round_ + TIMEOUT, t, a) for t in self.claims[a]]

    def join(self, joiners, _round):
        for a in joiners:
            self.active.add(a)
            self.claim(a)

    def tick(self, round_):
        due = [o for o in self.orphans if o[0] <= round_]
        self.orphans = [o for o in self.orphans if o[0] > round_]
        for _, t, a in due:
            self.claims[a].remove(t)
            self.touched[t].discard(a)
            self.free[t] += 1
            heapq.heappush(self.heap, t)
        for a in sorted(self.active):  # released tasks are taken by whoever has room
            self.claim(a)

    def pending(self):
        return bool(self.orphans)

    def open_loads(self):
        return [len(self.claims[a]) for a in self.active]

    def stranded(self, leavers):
        return sum(len(self.claims[a]) for a in leavers)


class App:
    """The real distribution code on the current app's database (call inside an app context)."""

    def __init__(self, sc):
        self.sc = sc
        db.session.remove()
        db.drop_all()
        db.create_all()
        self.project = Project(name="Simulation", desc="made-up data", is_annotation_project=True,
                               answers_per_task=sc.r, max_questions_per_user=1_000_000)
        self.group = UserGroup(name="Annotators")
        db.session.add_all([self.project, self.group])
        db.session.flush()
        db.session.add(Question(question="Label?", project_id=self.project.id))
        db.session.add_all([ProjectImage(project_id=self.project.id, filename=f"{t}.png", original_filename=f"{t}.png",
                                         file_path=f"/sim/{t}.png") for t in range(sc.tasks)])
        db.session.add(GroupProject(group_id=self.group.id, project_id=self.project.id))
        self.users = {}
        self._add_people(range(sc.people))
        distribution.distribute(self.project)
        self.active = set(range(sc.people))

    def _add_people(self, indices):
        new = {a: User(full_name=f"Annotator {a}", email=f"sim{a}@example.com", role="end-user", password_hash="x")
               for a in indices}
        db.session.add_all(new.values())
        db.session.flush()
        db.session.add_all([UserGroupMembership(user_id=u.id, group_id=self.group.id) for u in new.values()])
        db.session.commit()
        self.users.update(new)
        return new

    def _open(self):
        return (db.session.query(TaskAssignment.id, TaskAssignment.user_id).join(AnnotationTask)
                .filter(AnnotationTask.project_id == self.project.id, TaskAssignment.is_completed.is_(False))
                .order_by(TaskAssignment.id))

    def work(self, _round):
        index = {u.id: a for a, u in self.users.items() if a in self.active}
        taken, chosen = {}, []
        for assignment_id, user_id in self._open():  # each person answers their oldest open tasks
            a = index.get(user_id)
            if a is not None and taken.get(a, 0) < self.sc.speeds[a]:
                taken[a] = taken.get(a, 0) + 1
                chosen.append(assignment_id)
        for start in range(0, len(chosen), 500):
            for assignment in (TaskAssignment.query.options(joinedload(TaskAssignment.task))
                               .filter(TaskAssignment.id.in_(chosen[start:start + 500]))):
                assignment.is_completed = True
                assignment.task.current_answers += 1
                assignment.task.is_completed = assignment.task.current_answers >= assignment.task.required_answers
        db.session.commit()
        return len(chosen)

    def depart(self, leavers, _round):
        for a in leavers:
            user = self.users[a]
            UserGroupMembership.query.filter_by(user_id=user.id, group_id=self.group.id).delete()
            db.session.commit()
            distribution.on_member_removed(user, self.group.id)
            self.active.discard(a)

    def join(self, joiners, _round):
        for a, user in self._add_people(joiners).items():
            distribution.on_member_added(user, self.group.id)
            self.active.add(a)

    def tick(self, _round):
        pass

    def pending(self):
        return False

    def open_loads(self):
        return [row["open"] for row in distribution.workload_summary(self.project)]

    def stranded(self, leavers):
        ids = [self.users[a].id for a in leavers]
        return self._open().filter(TaskAssignment.user_id.in_(ids)).count()

    @property
    def answers(self):
        by_user = dict(db.session.query(TaskAssignment.user_id, func.count(TaskAssignment.id))
                       .filter(TaskAssignment.is_completed.is_(True)).group_by(TaskAssignment.user_id))
        return {a: by_user.get(u.id, 0) for a, u in self.users.items()}

    def duplicates(self):
        return (db.session.query(TaskAssignment.task_id).group_by(TaskAssignment.task_id, TaskAssignment.user_id)
                .having(func.count(TaskAssignment.id) > 1).count())


MODELS = {"static": Static, "self": SelfSelection, "app": App}
LABELS = {"static": "Static partitioning", "self": "Self-selection", "app": "App's procedure"}


def duplicates(model):
    if hasattr(model, "duplicates"):
        return model.duplicates()
    return sum(len(people) != len(set(people)) for people in model.given.values())


def spread(values):
    return max(values) - min(values) if values else 0


def simulate(name, sc):
    """Run one model on one scenario; return its measurements."""
    model = MODELS[name](sc)
    total, given, round_ = sc.tasks * sc.r, 0, 0
    left = joined = False
    result = {"model": name, "r": sc.r, "seed": sc.seed}
    while given < total:
        round_ += 1
        done = model.work(round_)
        given += done
        if not left and given >= LEAVE_AT * total:
            model.depart(sc.leavers, round_)
            left = True
            result["stranded_leave"] = model.stranded(sc.leavers)
            result["spread_leave"] = spread(model.open_loads())
        if left and not joined and given >= JOIN_AT * total:
            model.join(sc.joiners, round_)
            joined = True
            result["spread_join"] = spread(model.open_loads())
        model.tick(round_)
        if (done == 0 and not model.pending()) or round_ >= MAX_ROUNDS:
            break  # nobody can do anything more: the rest is stranded for good
    answers = sorted(model.answers.values(), reverse=True)
    top = math.ceil(0.1 * len(answers))  # the busiest 10% of everyone who took part
    result.update({
        "stranded_end": model.stranded(sc.leavers),
        "top10_share": sum(answers[:top]) / max(1, sum(answers)),
        "rounds": round_ if given >= total else None,
        "duplicates": duplicates(model),
        "answered": given, "total": total,
    })
    return result


def summarise(results):
    """Mean, min and max of each measurement over the seeds; rounds also count the runs that never finished."""
    def stats(values):
        return {"mean": statistics.mean(values), "min": min(values), "max": max(values)}

    summary = {"model": results[0]["model"], "r": results[0]["r"], "seeds": len(results)}
    for key in ("stranded_leave", "stranded_end", "spread_leave", "spread_join", "top10_share", "duplicates"):
        summary[key] = stats([res[key] for res in results])
    finished = [res["rounds"] for res in results if res["rounds"] is not None]
    summary["rounds"] = stats(finished) if finished else None
    summary["never"] = len(results) - len(finished)
    return summary


def cell(s, percent=False):
    def fmt(x):
        if percent:
            return f"{100 * x:.1f}%"
        return f"{x:.0f}" if float(x).is_integer() else f"{x:.1f}"
    return f"{fmt(s['mean'])} ({fmt(s['min'])}–{fmt(s['max'])})"


def rounds_cell(s):
    if s["rounds"] is None:
        return "never"
    text = cell(s["rounds"])
    return text if not s["never"] else f"{text}; never in {s['never']} of {s['seeds']}"


def report(summaries, tasks, people, joiners):
    lines = [f"## Allocation models: {tasks:,} tasks, {people} annotators, 10% leave at 40%, "
             f"{joiners} join at 60%", "",
             "| Model | r | Stranded after leave | Stranded at end | Open-load spread after leave "
             "| Open-load spread after join | Answers by busiest 10% | Rounds to finish | Duplicates |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summaries:
        lines.append(f"| {LABELS[s['model']]} | {s['r']} | {cell(s['stranded_leave'])} | {cell(s['stranded_end'])} "
                     f"| {cell(s['spread_leave'])} | {cell(s['spread_join'])} | {cell(s['top10_share'], True)} "
                     f"| {rounds_cell(s)} | {cell(s['duplicates'])} |")
    lines += ["", f"Mean (min–max) over {summaries[0]['seeds']} seeds. Stranded = unfinished tasks held by "
                  "someone who has left. Open-load spread = most minus fewest unfinished tasks per active annotator. "
                  "'Never' = some work stays stranded for good."]
    return "\n".join(lines)


def run(models, rs, seeds, tasks, people, joiners, first_seed=1, progress=None):
    """Every model and r over `seeds` seeds; call inside an app context (the app model uses its database)."""
    summaries, everything = [], []
    for r in rs:
        scenarios = [make_scenario(first_seed + n, tasks, people, r, joiners) for n in range(seeds)]
        for name in models:
            results = []
            for sc in scenarios:
                if progress:
                    progress(f"{name}, r = {r}, seed {sc.seed}")
                results.append(simulate(name, sc))
            summaries.append(summarise(results))
            everything += results
    return summaries, everything


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", type=int, default=5_000)
    parser.add_argument("--people", type=int, default=50)
    parser.add_argument("--joiners", type=int, default=5)
    parser.add_argument("--r", type=int, nargs="*", default=[1, 2])
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--models", nargs="*", default=list(MODELS), choices=list(MODELS))
    args = parser.parse_args(argv)

    app = create_app(TestingConfig)  # in-memory SQLite, as in the tests
    with app.app_context():
        summaries, _ = run(args.models, args.r, args.seeds, args.tasks, args.people, args.joiners, args.first_seed,
                           progress=lambda text: print(f"Running {text}...", file=sys.stderr, flush=True))
    text = report(summaries, args.tasks, args.people, args.joiners)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(text + "\n")
    return summaries


if __name__ == "__main__":
    main()
