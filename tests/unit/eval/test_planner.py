"""The scripted planners and the run loop, against a toolbox that needs no warehouse."""

from __future__ import annotations

import pytest

from omw.eval.planner import (
    AgentAnswer,
    RefuserPlanner,
    ScriptedPlanner,
    answer_metrics,
    answer_sql,
    number,
)
from omw.eval.questions.base import MetricStep, Question, task_of
from omw.eval.runner import run_eval
from omw.eval.tools import InfraError, ToolError
from omw.eval.world import SEP, World
from tests.unit.eval.conftest import FakeToolbox, metric_answer

USD = {"currency": "USD"}


def question(**fields: object) -> Question:
    base: dict[str, object] = {"id": "q", "category": "spend", "text": "?", "shape": "scalar", "unit": "USD"}
    return Question(**(base | fields))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("cell", "value"),
    [("12.50", 12.5), (3, 3.0), (None, None), ("n/a", None), (float("nan"), None), (True, None), (0, 0.0)],
)
def test_result_cells_become_numbers_or_nothing(cell: object, value: float | None) -> None:
    assert number(cell) == value


def test_sql_answers_take_the_shape_the_question_asks_for() -> None:
    cell = FakeToolbox("sql", [{"columns": ["s"], "rows": [["1234.500000000"]]}])
    assert answer_sql(question(), cell, "SELECT 1", "restated").to_dict() == {
        "kind": "answer", "value": 1234.5, "caveats": ["restated"],
    }  # fmt: skip
    rows = FakeToolbox(
        "sql", [{"columns": ["k", "v"], "rows": [["a", "1.5"], ["", 2], ["2026-09-01", None]]}]
    )
    table = answer_sql(question(shape="table"), rows, "SELECT 1")
    assert table.rows == [("a", 1.5), ("(none)", 2.0), ("2026-09-01", None)]
    name = FakeToolbox("sql", [{"columns": ["k"], "rows": [["Gadgets Launch"]]}])
    assert answer_sql(question(shape="label"), name, "SELECT 1").label == "Gadgets Launch"
    empty = FakeToolbox("sql", [{"columns": ["s"], "rows": []}])
    assert answer_sql(question(), empty, "SELECT 1").value is None


def test_a_query_the_warehouse_rejects_is_a_refusal_and_a_dead_warehouse_is_not() -> None:
    rejected = FakeToolbox("sql", [ToolError("ACCESS_DENIED on raw.t")])
    refused = answer_sql(question(), rejected, "SELECT 1")
    assert refused.kind == "refuse" and "ACCESS_DENIED" in refused.reason
    with pytest.raises(InfraError):
        answer_sql(question(), FakeToolbox("sql", [InfraError("QUOTA_EXCEEDED")]), "SELECT 1")
    with pytest.raises(InfraError):
        step = MetricStep(("meta_spend",), SEP)
        answer_metrics(question(), FakeToolbox("metrics", [InfraError("QUOTA_EXCEEDED")]), (step,))


def test_metric_steps_read_scale_ratios_sums_and_one_row() -> None:
    box = FakeToolbox("metrics", [metric_answer(["meta_ctr"], [[0.0125]], "disclose", caveats=["restated"])])
    rate = answer_metrics(question(), box, (MetricStep(("meta_ctr",), SEP, scale=100),))
    assert rate.value == pytest.approx(1.25) and rate.caveats == ["restated"]
    assert box.calls[0].arguments == {"metrics": ["meta_ctr"], "time_range": SEP.iso()}

    box = FakeToolbox("metrics", [metric_answer(["won", "all"], [[3, 12]])])
    share = answer_metrics(question(), box, (MetricStep(("won", "all"), SEP, divide_by="all", scale=100),))
    assert share.value == pytest.approx(25.0)

    box = FakeToolbox("metrics", [metric_answer(["a", "b", "c"], [[1, "2", None]])])
    assert (
        answer_metrics(question(), box, (MetricStep(("a", "b", "c"), SEP, add=("a", "b", "c")),)).value == 3.0
    )

    box = FakeToolbox("metrics", [metric_answer(["campaign_key", "ad_spend"], [["x", 5], ["y", 7]])])
    step = MetricStep(("ad_spend",), SEP, ("campaign_key",), USD, label="campaign_key", only="y")
    assert answer_metrics(question(), box, (step,)).value == 7.0
    assert box.calls[0].arguments["filters"] == USD and box.calls[0].arguments["dimensions"] == [
        "campaign_key"
    ]


def test_metric_tables_rank_and_name_and_compare() -> None:
    rows = [["a", 1], ["b", 9], ["c", 5], ["", 7]]
    step = MetricStep(("clicks",), SEP, ("query",), label="query")
    top = answer_metrics(
        question(shape="table", top=2),
        FakeToolbox("metrics", [metric_answer(["query", "clicks"], rows)]),
        (step,),
    )
    assert top.rows == [("b", 9.0), ("(none)", 7.0)]
    best = answer_metrics(
        question(shape="label"), FakeToolbox("metrics", [metric_answer(["query", "clicks"], rows)]), (step,)
    )
    assert best.label == "b"

    replies = [metric_answer(["spend"], [["120.0"]]), metric_answer(["spend"], [["100.0"]])]
    steps = (MetricStep(("spend",), SEP, alias="current"), MetricStep(("spend",), SEP, alias="previous"))
    both = answer_metrics(question(shape="table"), FakeToolbox("metrics", list(replies)), steps)
    assert both.rows == [("current", 120.0), ("previous", 100.0)]
    moved = answer_metrics(question(combine="pct_change"), FakeToolbox("metrics", list(replies)), steps)
    assert moved.value == pytest.approx(20.0)

    period = [metric_answer(["period", "clicks"], [["current", 8], ["previous", 4]])]
    step = MetricStep(("clicks",), "last_7_days", compare="previous_period", label="period")
    compared = answer_metrics(question(shape="table"), FakeToolbox("metrics", period), (step,))
    assert compared.rows == [("current", 8.0), ("previous", 4.0)]
    assert step.request() == {
        "metrics": ["clicks"],
        "time_range": "last_7_days",
        "compare": "previous_period",
    }


def test_the_layers_refusal_or_question_back_is_passed_on() -> None:
    for mood in ("refuse", "clarify"):
        box = FakeToolbox("metrics", [metric_answer([], [], mood, reason="because")])
        passed = answer_metrics(question(), box, (MetricStep(("clicks",), SEP),))
        assert passed.kind == mood and passed.reason == "because"


def test_the_reference_planner_follows_the_reference_plans() -> None:
    plain = question(sql="SELECT ref", naive_sql="SELECT naive", metrics=(MetricStep(("meta_spend",), SEP),))
    trap = question(id="t", expect="refuse", why="not additive", naive_sql="SELECT sum(reach)")
    gap = question(id="g", sql="SELECT ref")
    planner = ScriptedPlanner({q.id: q for q in (plain, trap, gap)}, "reference")
    assert planner.name == "reference"
    sql = FakeToolbox("sql", [{"columns": ["v"], "rows": [[1]]}])
    assert planner.solve(task_of(plain), sql).value == 1.0 and sql.calls[0].arguments == {
        "query": "SELECT ref"
    }
    declined = planner.solve(task_of(trap), FakeToolbox("sql"))
    assert declined.kind == "refuse" and declined.reason == "not additive"
    missing = planner.solve(task_of(gap), FakeToolbox("metrics"))
    assert missing.kind == "refuse" and "no metric" in missing.reason


def test_the_literal_planner_takes_the_question_at_face_value() -> None:
    plain = question(sql="SELECT ref", naive_sql="SELECT naive", sql_caveat="restated")
    trap = question(
        id="t",
        expect="refuse",
        why="no",
        naive_sql="SELECT sum(reach)",
        naive_metrics=(MetricStep(("meta_reach",), SEP),),
    )
    absent = question(id="a", expect="refuse", why="not loaded")
    planner = ScriptedPlanner({q.id: q for q in (plain, trap, absent)}, "literal")
    sql = FakeToolbox("sql", [{"columns": ["v"], "rows": [[1]]}, {"columns": ["v"], "rows": [[2]]}])
    first = planner.solve(task_of(plain), sql)
    assert sql.calls[0].arguments == {"query": "SELECT naive"} and first.caveats == []
    assert planner.solve(task_of(trap), sql).value == 2.0  # the wrong number the trap is there to catch
    assert planner.solve(task_of(absent), FakeToolbox("sql")).kind == "refuse"
    layer = FakeToolbox("metrics", [metric_answer([], [], "refuse", reason="not additive")])
    assert planner.solve(task_of(trap), layer).kind == "refuse"
    assert planner.solve(task_of(absent), FakeToolbox("metrics")).kind == "refuse"


def test_a_run_scores_every_question_in_every_arm_and_records_failures_as_errors(world: World) -> None:
    good = question(id="good", truth=lambda w: 5.0, sql="SELECT 5")
    broken = question(id="broken", truth=lambda w: 5.0, sql="SELECT 5")
    trap = question(id="trap", category="trap", expect="clarify", why="which")
    sql = FakeToolbox("sql", [{"columns": ["v"], "rows": [[5]]}, InfraError("QUOTA_EXCEEDED")])
    planner = ScriptedPlanner({q.id: q for q in (good, broken, trap)}, "reference")
    run = run_eval(planner, [sql], [good, broken, trap], world, stamp=False)
    assert [row.score.outcome for row in run.rows] == ["correct", "error", "correct"]
    summary = run.summary("sql")
    assert (summary.answerable, summary.traps, summary.errors, summary.accuracy) == (1, 1, 1, 1.0)
    payload = run.to_dict()
    assert payload["planner"] == "reference" and payload["started"] == ""
    assert payload["rows"][0]["calls"][0]["arguments"] == {"query": "SELECT 5"}
    assert payload["rows"][1]["answer"]["kind"] == "error" and "QUOTA" in payload["rows"][1]["detail"]
    assert payload["summary"]["sql"]["errors"] == 1 and run.categories == ["spend", "trap"]
    assert run.summary("sql", "trap").correct_traps == 1


def test_the_refuser_never_gives_a_number_right_or_wrong(world: World) -> None:
    good = question(id="good", truth=lambda w: 5.0, sql="SELECT 5")
    trap = question(id="trap", category="trap", expect="refuse", why="no")
    run = run_eval(
        RefuserPlanner(), [FakeToolbox("sql"), FakeToolbox("metrics")], [good, trap], world, reps=2
    )
    assert len(run.rows) == 8 and run.arms == ["sql", "metrics"]
    for arm in run.arms:
        summary = run.summary(arm)
        assert (
            summary.accuracy == 0.0 and summary.trap_correctness == 1.0 and summary.wrong_number_rate == 0.0
        )
    assert isinstance(run.rows[0].answer, AgentAnswer) and run.started
