"""The evaluation harness against the real stack: the seed through the repo's own marts views, both arms'
reference plans against the independent ground truth, and the published scripted results reproduced."""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from clickhouse_connect.driver.client import Client

from omw.eval import report
from omw.eval.generate import build_world
from omw.eval.planner import ScriptedPlanner
from omw.eval.questions import all_questions
from omw.eval.questions.base import Question
from omw.eval.runner import Run, run_eval
from omw.eval.seed import SeedReport, drop, seed, seeded, unexpected_verdicts
from omw.eval.tools import (
    SqlToolbox,
    Toolbox,
    ToolError,
    cached,
    direct_runner,
    mcp_runner,
    metrics_toolbox,
)
from omw.eval.world import World
from tests.integration.conftest import setting

pytestmark = pytest.mark.integration

GAPS = {"not_in_catalog", "grain_refused"}


@pytest.fixture(scope="module")
def warehouse(pipeline_client: Client) -> Iterator[tuple[World, SeedReport]]:
    world = build_world()
    loaded = seed(pipeline_client, world, force=True)  # the test stack is disposable
    yield world, loaded
    drop(pipeline_client)


@pytest.fixture(scope="module")
def toolboxes(warehouse: tuple[World, SeedReport], agent_client: Client) -> list[Toolbox]:
    """One set of tools for the module: repeated queries are answered from memory, so the whole file costs
    the agent a few hundred of its 1000 queries an hour."""
    sql = SqlToolbox(cached(mcp_runner(setting("OMW_MCP_URL"), setting("OMW_MCP_AUTH_TOKEN"))))
    return [sql, metrics_toolbox(agent_client)]


@pytest.fixture(scope="module")
def questions() -> list[Question]:
    return all_questions()


@pytest.fixture(scope="module")
def runs(
    warehouse: tuple[World, SeedReport], toolboxes: list[Toolbox], questions: list[Question]
) -> dict[str, Run]:
    plans = {question.id: question for question in questions}
    return {
        mode: run_eval(ScriptedPlanner(plans, mode), toolboxes, questions, warehouse[0], stamp=False)  # type: ignore[arg-type]
        for mode in ("reference", "literal")
    }


def test_the_seed_loads_every_source_and_the_real_checks_agree(
    warehouse: tuple[World, SeedReport], pipeline_client: Client
) -> None:
    world, loaded = warehouse
    assert loaded.tables == 49 and loaded.rows > 30_000 and loaded.views >= 35 and seeded(pipeline_client)
    # Every over-count and row-count check passes on the synthetic data; only GA4 is stale, by design.
    assert unexpected_verdicts(loaded.checks) == []
    assert len(loaded.checks.results) >= 25 and len(loaded.checks.failed) == 1


def test_every_marts_view_returns_what_the_ground_truth_was_computed_from(
    warehouse: tuple[World, SeedReport], agent_client: Client
) -> None:
    """The views in sql/ over the seeded raw tables against rows generated independently of them."""
    world, _ = warehouse
    assert len(world.tables) >= 30
    for table, rows in world.tables.items():
        numeric = [
            c for c, v in rows[0].items() if isinstance(v, int | float | Decimal) and not isinstance(v, bool)
        ]
        text = [c for c, v in rows[0].items() if isinstance(v, str | bool)]
        select = ["count()", *(f"sum({c})" for c in numeric), *(f"uniqExact({c})" for c in text)]
        got = agent_client.query(f"SELECT {', '.join(select)} FROM marts.{table}").result_rows[0]
        want: list[Any] = [len(rows)]
        want += [sum(row[c] for row in rows) for c in numeric]
        want += [len({row[c] for row in rows}) for c in text]
        assert [float(v) for v in got] == pytest.approx([float(v) for v in want], rel=1e-9), table


def test_the_agent_reads_the_campaign_keys_the_pipeline_derived(
    warehouse: tuple[World, SeedReport], agent_client: Client
) -> None:
    world, _ = warehouse
    rows = agent_client.query(
        "SELECT source, native_id, argMax(campaign_key, updated_at), argMax(origin, updated_at) "
        "FROM marts.campaign_keys GROUP BY source, native_id"
    ).result_rows
    assert {(source, native_id): key for source, native_id, key, _ in rows} == world.campaign_keys
    assert [key for *_, key, origin in rows if origin == "manual"] == ["abm_tier_1"]


def test_reference_sql_matches_the_ground_truth_through_mcp_clickhouse(runs: dict[str, Run]) -> None:
    rows = runs["reference"].of_arm("sql")
    wrong = [
        (row.question.id, row.score.outcome, row.score.detail)
        for row in rows
        if row.score.outcome != "correct"
    ]
    assert wrong == []
    summary = runs["reference"].summary("sql")
    assert (
        summary.accuracy == 1.0
        and summary.trap_correctness == 1.0
        and summary.disclosed == summary.need_caveat
    )


def test_the_metric_layer_matches_the_ground_truth_wherever_it_answers(runs: dict[str, Run]) -> None:
    rows = runs["reference"].of_arm("metrics")
    missed = {row.question.id: row for row in rows if row.score.outcome != "correct"}
    gaps = {row.question.id for row in rows if GAPS & set(row.question.tags)}
    assert set(missed) == gaps and 5 <= len(gaps) <= 15
    assert {row.score.outcome for row in missed.values()} == {"over_refusal"}  # never a wrong number
    summary = runs["reference"].summary("metrics")
    assert summary.wrong_numbers == 0 and summary.disclosed == summary.need_caveat == 5


def test_face_value_requests_are_wrong_far_more_often_through_raw_sql(runs: dict[str, Run]) -> None:
    sql, metrics = runs["literal"].summary("sql"), runs["literal"].summary("metrics")
    assert sql.wrong_numbers >= 3 * metrics.wrong_numbers > 0
    assert metrics.trap_correctness >= sql.trap_correctness + 0.4
    assert (
        sql.disclosed == 0 and metrics.disclosed == metrics.need_caveat
    )  # caveats only travel with the layer
    # What the layer does with a face-value request is never worse than raw SQL on the same question.
    by_arm = {(row.question.id, row.arm): row.score.outcome for row in runs["literal"].rows}
    worse = [qid for (qid, arm), outcome in by_arm.items() if arm == "metrics" and outcome == "wrong_number"
             and by_arm[(qid, "sql")] == "correct"]  # fmt: skip
    assert worse == ["meta_spend_sep_vs_aug"]  # compare: month is not the calendar month before


def test_the_boundary_holds_for_the_face_value_sql(runs: dict[str, Run]) -> None:
    rows = {row.question.id: row for row in runs["literal"].of_arm("sql")}
    for question_id, marker in (
        ("lead_email_addresses", "ACCESS_DENIED"),
        ("unpaid_invoices", "ACCESS_DENIED"),
        ("pause_campaign", ""),
    ):
        row = rows[question_id]
        assert row.score.outcome == "correct" and row.answer.kind == "refuse", question_id
        assert row.calls[0]["error"] and marker in row.calls[0]["error"], row.calls[0]["error"]


def test_the_published_scripted_results_are_what_a_fresh_run_gives(runs: dict[str, Run]) -> None:
    published = {run["planner"]: run for run in json.loads(report.SCRIPTED_FILE.read_text())}
    for mode, run in runs.items():
        fresh = {(row.question.id, row.arm): row.score.outcome for row in run.rows}
        recorded = {(row["id"], row["arm"]): row["outcome"] for row in published[mode]["rows"]}
        changed = {
            key: (recorded.get(key), outcome)
            for key, outcome in fresh.items()
            if recorded.get(key) != outcome
        }
        assert changed == {}, "run `uv run omw eval report` and commit eval/"
        assert recorded.keys() == fresh.keys()


def test_both_sql_transports_give_the_same_answers(
    warehouse: tuple[World, SeedReport],
    agent_client: Client,
    toolboxes: list[Toolbox],
    questions: list[Question],
) -> None:
    sample = [q for q in questions if q.sql][::12]
    direct = SqlToolbox(direct_runner(agent_client))
    for question in sample:
        assert question.sql is not None
        assert direct.call("run_query", {"query": question.sql}) == toolboxes[0].call(
            "run_query", {"query": question.sql}
        )
    with pytest.raises(ToolError, match="ACCESS_DENIED|Not enough privileges"):
        direct.call("run_query", {"query": "SELECT count() FROM raw.stripe___invoices"})


def test_the_sql_arm_is_given_the_schema_the_agent_can_see(toolboxes: list[Toolbox]) -> None:
    context = toolboxes[0].context()
    assert "marts.meta_ads_campaigns" in context and "ops.freshness" in context
    assert "A rate: never sum it" in context and "raw." not in context.replace("stays in raw.", "")
