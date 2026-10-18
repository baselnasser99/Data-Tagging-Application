"""The evaluation script (scripts/benchmark.py) runs end to end on a tiny made-up project."""
import importlib.util
import pathlib
import random

spec = importlib.util.spec_from_file_location(
    "benchmark", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_benchmark_scenario_is_fair_and_loses_nothing(app, monkeypatch):
    monkeypatch.setitem(benchmark.SCENARIOS, 100, (10, 10))  # 10 people, 10 items x 10 questions
    row = benchmark.run_scenario(100, random.Random(1))
    assert row["owned"] and row["kept"]  # every task has its owner; leaving kept finished work
    assert row["gap_total"] <= 1 and row["gap_open_join"] <= 1  # loads within one task
    table = benchmark.report([row], None, "sqlite")
    assert "| 100 | 10 |" in table
