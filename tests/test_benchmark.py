"""The evaluation script (scripts/benchmark.py) runs end to end on tiny made-up projects."""
import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location(
    "benchmark", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)

TINY = {  # 10 people, 10 items x 10 questions = 100 tasks
    "tiny": {"people": 10, "items": 10, "r": 1, "groups": 1},
    "tiny-r2": {"people": 10, "items": 10, "r": 2, "groups": 1},
    "tiny-2groups": {"people": 10, "items": 10, "r": 1, "groups": 2},
}


@pytest.fixture(autouse=True)
def tiny_configs(monkeypatch):
    for name, config in TINY.items():
        monkeypatch.setitem(benchmark.CONFIGS, name, config)


@pytest.mark.parametrize("name", list(TINY))
def test_every_configuration_is_fair_and_loses_nothing(app, name):
    row = benchmark.run_config(name, seed=1)
    assert row["tasks"] == 100 and row["r"] == TINY[name]["r"] and row["groups"] == TINY[name]["groups"]
    assert row["owned"] and row["kept"]  # every task has its owners; leaving kept finished work
    assert row["gap_total"] <= 1 and row["gap_open_join"] <= 1  # loads within one task


def test_two_answers_per_task_means_two_people_per_task(app):
    benchmark.run_config("tiny-r2", seed=1)
    assert benchmark.TaskAssignment.query.count() == 200
    assert all(t.required_answers == 2 for t in benchmark.AnnotationTask.query)


def test_two_groups_share_one_project(app):
    benchmark.run_config("tiny-2groups", seed=1)
    assert benchmark.GroupProject.query.count() == 2
    teams = {m.group_id for m in benchmark.UserGroupMembership.query}
    assert len(teams) == 2  # people, leavers and newcomers are spread over both groups
    holders = {a.user_id for a in benchmark.TaskAssignment.query}
    members = {m.user_id for m in benchmark.UserGroupMembership.query}
    assert members <= holders  # everyone in either group has work


def test_summarise_reports_mean_spread_and_integrity_counts():
    runs = [{"config": "x", "tasks": 100, "people": 10, "r": 1, "groups": 1, "leavers": 1, "joiners": 1,
             "handout_s": t, "leave_s": 1.0, "join_s": 1.0, "gap_total": g, "gap_open_start": 4,
             "gap_open_leave": 2, "gap_open_join": 1, "owned": True, "kept": k}
            for t, g, k in [(1.0, 0, True), (2.0, 1, True), (3.0, 2, False)]]
    s = benchmark.summarise(runs)
    assert s["runs"] == 3
    assert s["handout_s"] == {"mean": 2.0, "min": 1.0, "max": 3.0, "sd": 1.0, "n": 3}
    assert s["gap_total"]["mean"] == 1 and s["gap_open_start"]["sd"] == 0
    assert (s["owned"], s["kept"]) == (3, 2)
    assert benchmark.cell(s["handout_s"], " s") == "2.00 s (1.00–3.00), sd 1.00"
    assert benchmark.cell(s["gap_total"], digits=0) == "1 (0–2), sd 1.0"
    assert benchmark.spread([5])["sd"] == 0.0  # a single run has no spread


def test_each_run_gets_its_own_seed_and_the_report_shows_the_spread(app):
    summaries, runs = benchmark.run_all(["tiny", "tiny-2groups"], runs=2, seed=7)
    assert [r["seed"] for r in runs] == [7, 8, 7, 8]
    table = benchmark.report(summaries, None, "sqlite", runs)
    assert "| 100 | 10 | 1 | 1 | 2 | 1 / 1 |" in table  # tasks, people, r, groups, runs, leavers / newcomers
    assert "| 100 | 10 | 1 | 2 | 2 |" in table
    assert "2/2 | 2/2 |" in table and "Every run" in table


def test_main_runs_from_the_command_line():
    summaries, load = benchmark.main(["--configs", "tiny", "--runs", "2", "--seed", "3"])
    assert load is None and summaries[0]["runs"] == 2 and summaries[0]["owned"] == 2
