"""The allocation simulation (scripts/simulate_allocation.py) runs all three models on a tiny scenario."""
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "simulate_allocation", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "simulate_allocation.py")
simulation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(simulation)


def test_apps_procedure_strands_nothing_and_never_duplicates(app):
    summaries, results = simulation.run(list(simulation.MODELS), rs=[1, 2], seeds=2, tasks=60, people=10, joiners=1)
    assert {(s["model"], s["r"]) for s in summaries} == {(m, r) for m in simulation.MODELS for r in (1, 2)}
    for res in results:
        assert res["duplicates"] == 0, res  # nobody answers the same task twice, in any model
        if res["model"] == "app":
            assert res["stranded_leave"] == 0 and res["stranded_end"] == 0, res
            assert res["rounds"] is not None  # all work gets done
    static = [res for res in results if res["model"] == "static"]
    assert all(res["stranded_end"] > 0 and res["rounds"] is None for res in static)  # a leaver's work stays stuck
    table = simulation.report(summaries, 60, 10, 1)
    assert "| App's procedure | 2 | 0 (0–0) | 0 (0–0) |" in table and "never" in table
