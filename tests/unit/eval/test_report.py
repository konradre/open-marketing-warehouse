"""The report: tables recomputed from per-question rows, and a README whose results cannot drift from the
recorded run they claim to show."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from omw.cli import main
from omw.eval import report
from omw.eval.cli import add_parser
from omw.eval.planner import RefuserPlanner, ScriptedPlanner
from omw.eval.questions.base import Question
from omw.eval.runner import run_eval
from omw.eval.tools import ToolError
from omw.eval.world import World
from tests.unit.eval.conftest import FakeToolbox


def row(question_id: str, arm: str, outcome: str, **more: Any) -> dict[str, Any]:
    base = {"id": question_id, "arm": arm, "category": "spend", "tags": [], "expect": "answer"}
    return base | {"outcome": outcome, "detail": "", "disclosed": None} | more


RUN = {
    "planner": "literal",
    "rows": [
        row("a", "sql", "correct"),
        row("b", "sql", "wrong_number", detail="got 2, want 1"),
        row("t", "sql", "wrong_number", category="trap", expect="refuse", tags=["non_additive"]),
        row("a", "metrics", "correct", disclosed=True),
        row("b", "metrics", "over_refusal", detail="refuse: no metric | in the catalog"),
        row("t", "metrics", "correct", category="trap", expect="refuse", tags=["non_additive"]),
    ],
}
REFERENCE = {
    "planner": "reference",
    "rows": [r | {"outcome": "correct"} for r in RUN["rows"][:3]] + RUN["rows"][3:],
}


def test_the_headline_is_recomputed_from_rows() -> None:
    table = report.headline([RUN])
    sql, metrics = table.splitlines()[2:]
    assert "schema + raw SQL" in sql and "50.0% (1/2;" in sql and "0.0% (0/1;" in sql and "66.7% (2/3;" in sql
    assert (
        "SEMANTICS.md + query_metrics" in metrics
        and "100.0% (1/1;" in metrics
        and "| 1/2 | 1/1 | 0 |" in metrics
    )
    assert "face-value plans" in table


def test_breakdowns_by_category_and_by_kind_of_trap() -> None:
    assert "| spend | 2 | 1/2 | 1/2 | 1 | 0 |" in report.by_category(RUN)
    assert "| trap | 1 | 0/1 | 1/1 | 1 | 0 |" in report.by_category(RUN)
    assert "| non additive | 1 | 0/1 | 1/1 |" in report.traps_by_kind(RUN)


def test_misses_name_the_question_and_survive_a_pipe_in_the_detail() -> None:
    table = report.misses(RUN, "metrics")
    assert "| `b` | over refusal | refuse: no metric / in the catalog |" in table
    assert report.misses(RUN, "metrics", frozenset({"b"})) == "None."
    markdown = report.results_markdown([REFERENCE, RUN])
    assert markdown.index("Face-value plans, by category") < markdown.index(
        "could not answer with the best plan"
    )
    assert "Beyond the coverage gaps in the next table.\n\nNone." in markdown  # b is a gap in both runs


def test_the_readme_keeps_everything_outside_the_markers(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(f"# Title\n\nintro\n\n{report.BEGIN}\nold\n{report.END}\n\n## After\n")
    report.update_readme("| new |", readme)
    text = readme.read_text()
    assert text.startswith("# Title\n\nintro\n") and text.endswith("\n## After\n")
    assert "| new |" in text and "old" not in text
    readme.write_text("no markers")
    with pytest.raises(ValueError, match="markers"):
        report.update_readme("x", readme)


def test_a_recorded_run_is_slim_and_stable(world: World) -> None:
    question = Question(
        id="q",
        category="spend",
        text="?",
        shape="scalar",
        unit="USD",
        truth=lambda w: 1 / 3,
        sql="SELECT 1/3",
    )
    box = FakeToolbox("sql", [{"columns": ["v"], "rows": [[0.33333333333333337]]}])
    run = run_eval(ScriptedPlanner({"q": question}, "reference"), [box], [question], world, stamp=False)
    recorded = report.slim(run)
    only = recorded["rows"][0]
    assert only["answer"] == {"kind": "answer", "value": 0.3333333333} and "expected" not in only
    assert only["calls"] == [{"name": "run_query", "arguments": {"query": "SELECT 1/3"}, "error": ""}]
    assert json.loads(json.dumps(recorded)) == recorded and recorded["seed"] == world.seed


def test_a_recorded_run_does_not_name_the_stack_it_ran_on(world: World) -> None:
    trap = Question(
        id="t",
        category="trap",
        text="?",
        shape="scalar",
        unit="",
        expect="refuse",
        why="no",
        naive_sql="SELECT 1",
    )
    error = "Code: 497. Not enough privileges. (ACCESS_DENIED) (version 25.8.33.6 (official build)) (for url http://db:8123)"
    run = run_eval(
        ScriptedPlanner({"t": trap}, "literal"), [FakeToolbox("sql", [ToolError(error)])], [trap], world
    )
    text = json.dumps(report.slim(run))
    assert "ACCESS_DENIED" in text and "db:8123" not in text and "25.8" not in text


def test_recorded_runs_round_trip_with_one_row_per_line(tmp_path: Path) -> None:
    path = tmp_path / "results" / "model-x.json"
    report.write_runs(path, [RUN | {"started": "2026-10-02T10:00:00"}, REFERENCE | {"started": "2026-10-01"}])
    assert json.loads(path.read_text())[0]["rows"] == RUN["rows"]
    assert sum(line.startswith('  {"id"') for line in path.read_text().splitlines()) == 12
    assert [run["planner"] for run in report.recorded_model_runs(path.parent)] == ["reference", "literal"]


def test_the_question_file_carries_truth_and_no_plan(world: World) -> None:
    trap = Question(
        id="t", category="trap", text="Reach?", shape="scalar", unit="", expect="refuse", why="no"
    )
    plain = Question(
        id="q", category="spend", text="How much?", shape="scalar", unit="USD", truth=lambda w: 2.5,
        sql="SELECT secret", caveat_terms=("restat",),
    )  # fmt: skip
    payload = report.questions_payload([plain, trap], world)
    assert payload["count"] == 2 and payload["today"] == "2026-10-01"
    first, second = payload["questions"]
    assert first["ground_truth"] == {"value": 2.5} and first["needs_caveat"] and first["accept"] == ["answer"]
    assert second["ground_truth"] is None and second["why"] == "no"
    assert "secret" not in json.dumps(payload)


def test_the_committed_readme_shows_the_committed_run() -> None:
    """Numbers in eval/README.md come from eval/results/*.json, which a test in tests/integration re-runs."""
    runs = json.loads(report.SCRIPTED_FILE.read_text()) + report.recorded_model_runs()
    text = report.README.read_text()
    block = text.split(report.BEGIN, 1)[1].split(report.END, 1)[0].strip()
    assert block == report.results_markdown(runs).strip(), (
        "run `uv run omw eval report` and commit the result"
    )
    assert [run["planner"] for run in runs[:3]] == ["reference", "literal", "refuser"]


def test_the_committed_run_covers_every_question_in_both_arms(questions: list[Question]) -> None:
    ids = [question.id for question in questions]
    for run in json.loads(report.SCRIPTED_FILE.read_text()):
        for arm in ("sql", "metrics"):
            assert [r["id"] for r in run["rows"] if r["arm"] == arm] == ids, (run["planner"], arm)
        assert all(r["outcome"] != "error" for r in run["rows"])


def test_the_refuser_baseline_bounds_the_scores(world: World, questions: list[Question]) -> None:
    run = run_eval(RefuserPlanner(), [FakeToolbox("sql")], questions, world, stamp=False)
    summary = run.summary("sql")
    assert (
        summary.accuracy == 0.0 and summary.wrong_number_rate == 0.0 and 0.5 < summary.trap_correctness < 1.0
    )


def test_the_command_is_wired_into_omw(capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser()
    add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["eval", "run", "--planner", "literal", "--only", "trap", "--arms", "metrics"])
    assert (args.planner, args.only, args.arms, args.transport, args.reps) == (
        "literal",
        "trap",
        ["metrics"],
        "mcp",
        1,
    )
    assert parser.parse_args(["eval", "seed", "--force"]).force is True
    with pytest.raises(SystemExit):
        main(["eval", "run", "--planner", "nonsense"])
    assert "invalid choice" in capsys.readouterr().err
