"""Each check's SQL and verdicts, the runner, the ops SQL and the CLI exit code."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import dlt
import pytest

from omw import cli
from omw.checks import (
    DEFAULT_BUDGET_HOURS,
    OVERCOUNT_RULES,
    ROWCOUNT_RULES,
    Status,
    freshness_checks,
    overcount_checks,
    rowcount_checks,
    run_checks,
)
from omw.checks.overcount import OvercountRule, overcount_sql
from omw.checks.results import CHECK_RESULT_COLUMNS, CheckResult
from omw.checks.rowcount import RowcountRule, rowcount_sql
from omw.checks.runner import OPS_SQL, CheckRun
from omw.pipelines.marts import statements
from tests.unit.checks.conftest import FakeWarehouse

TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 6, 0, 0)
RULE = OvercountRule(
    "ga4", "marts.ga4_core", "marts.ga4_channels", "channels", ("property_id", "date"), "sessions"
)


def test_overcount_sql_compares_breakdown_to_core_per_group() -> None:
    sql = overcount_sql(RULE, date(2026, 9, 7))
    assert (
        "WITH core AS (SELECT property_id, date, sum(sessions) AS core_total FROM marts.ga4_core "
        "WHERE date >= '2026-09-07'" in sql
    )
    assert (
        "breakdown AS (SELECT property_id, date, sum(sessions) AS breakdown_total FROM marts.ga4_channels"
        in sql
    )
    assert "countIf(breakdown_total > core_total * 1.01) AS violations" in sql
    assert sql.endswith("FROM breakdown LEFT JOIN core USING (property_id, date)")


def test_overcount_verdicts(warehouse: FakeWarehouse) -> None:
    warehouse.when("marts.ga4_channels", [[30, 0, 1.0]])
    passed = overcount_checks(warehouse, TODAY, (RULE,))
    assert passed[0].status is Status.PASS and passed[0].observed == 1.0 and passed[0].report == "channels"
    assert "since" not in passed[0].detail and passed[0].expected == 1.01
    warehouse.answers.clear()
    warehouse.when("marts.ga4_channels", [[30, 2, 1.07]])
    failed = overcount_checks(warehouse, TODAY, (RULE,))
    assert failed[0].status is Status.FAIL and "2 of 30 groups" in failed[0].detail
    warehouse.answers.clear()
    warehouse.when("marts.ga4_channels", [[0, 0, None]])
    assert overcount_checks(warehouse, TODAY, (RULE,))[0].status is Status.WARN


def test_overcount_skips_sources_not_loaded_yet(warehouse: FakeWarehouse) -> None:
    results = overcount_checks(warehouse, TODAY, OVERCOUNT_RULES, existing_views={"marts.ga4_core"})
    assert results == [] and warehouse.queries == []


def test_every_overcount_rule_names_views_from_the_marts() -> None:
    for rule in OVERCOUNT_RULES:
        assert rule.core.startswith("marts.") and rule.breakdown.startswith("marts.") and "date" in rule.keys
    assert {r.source for r in OVERCOUNT_RULES} == {"search_console", "ga4", "google_ads", "meta_ads"}


def test_freshness_verdicts(warehouse: FakeWarehouse) -> None:
    objects = {
        "raw.meta_ads___sync_windows",
        "raw.ga4___sync_windows",
        "raw.stripe___sync_windows",
        "ops.pacing_events",
    }
    warehouse.when("ops.pacing_events", [["stripe", "token_expired", "token expires 2026-10-01 (-4 days)"]])
    warehouse.when("raw.meta_ads___sync_windows", [[datetime(2026, 10, 5, 3, 0), 12]])
    warehouse.when("raw.ga4___sync_windows", [[datetime(2026, 10, 2, 3, 0), 9]])
    warehouse.when("raw.stripe___sync_windows", [[datetime(2026, 10, 5, 4, 0), 3]])
    results = {r.source: r for r in freshness_checks(warehouse, NOW, DEFAULT_BUDGET_HOURS, objects)}
    assert results["meta_ads"].status is Status.PASS and results["meta_ads"].observed == 3.0
    assert (
        results["ga4"].status is Status.FAIL
        and "budget 30h" in results["ga4"].detail
        and results["ga4"].observed == 75.0
    )
    assert results["stripe"].status is Status.FAIL and "token expired" in results["stripe"].detail
    assert results["google_ads"].status is Status.FAIL and "never synced" in results["google_ads"].detail
    assert results["search_console"].expected == 48.0
    assert all(r.check == "freshness" and r.report == "sync" for r in results.values())


def test_freshness_without_pacing_events_table(warehouse: FakeWarehouse) -> None:
    warehouse.when("raw.ga4___sync_windows", [[None, 0]])
    results = freshness_checks(warehouse, NOW, {"ga4": 30.0}, {"raw.ga4___sync_windows"})
    assert results[0].status is Status.FAIL and "empty sync log" in results[0].detail
    assert not any("pacing_events" in q for q in warehouse.queries)


def test_rowcount_sql_and_verdicts(warehouse: FakeWarehouse) -> None:
    rule = RowcountRule("ga4", "marts.ga4_core", "core")
    sql = rowcount_sql(rule, TODAY)
    assert "countIf(date BETWEEN '2026-09-28' AND '2026-10-04') AS current_rows" in sql
    assert "countIf(date BETWEEN '2026-09-21' AND '2026-09-27') AS previous_rows FROM marts.ga4_core" in sql
    cases = [
        ([7, 7], Status.PASS),
        ([0, 7], Status.FAIL),
        ([0, 0], Status.WARN),
        ([30, 7], Status.WARN),
        ([7, 0], Status.PASS),
    ]
    for rows, expected in cases:
        warehouse.answers.clear()
        warehouse.when("marts.ga4_core", [rows])
        result = rowcount_checks(warehouse, TODAY, (rule,))[0]
        assert result.status is expected, (rows, result.detail)
        assert result.observed == rows[0] and result.expected == rows[1]
    assert {r.source for r in ROWCOUNT_RULES} == {
        "search_console",
        "ga4",
        "google_ads",
        "meta_ads",
        "stripe",
        "linkedin_ads",
    }


def test_runner_writes_results_and_reports_failures(warehouse: FakeWarehouse) -> None:
    warehouse.objects = {"raw.ga4___sync_windows", "marts.ga4_core", "marts.ga4_channels"}
    warehouse.when("raw.ga4___sync_windows", [[datetime(2026, 10, 5, 3, 0), 1]])
    warehouse.when("marts.ga4_channels", [[3, 1, 1.5]])
    warehouse.when("marts.ga4_core", [[7, 7]])
    run = run_checks(warehouse, NOW, {"ga4": 30.0})
    assert [(r.check, r.status) for r in run.results] == [
        ("freshness", Status.PASS),
        ("overcount", Status.FAIL),
        ("rowcount", Status.PASS),
    ]
    assert not run.ok and len(run.failed) == 1 and run.summary() == "2 pass, 0 warn, 1 fail"
    table, data, columns = warehouse.inserted[0]
    assert table == "ops.check_results" and columns == list(CHECK_RESULT_COLUMNS) and len(data) == 3
    assert data[1][1:5] == ["ga4", "overcount", "channels", "fail"]


def test_runner_filters_sources_and_can_skip_writing(warehouse: FakeWarehouse) -> None:
    warehouse.objects = {"raw.ga4___sync_windows", "raw.stripe___sync_windows"}
    warehouse.when("raw.ga4___sync_windows", [[datetime(2026, 10, 5, 3, 0), 1]])
    warehouse.when("raw.stripe___sync_windows", [[datetime(2026, 10, 5, 3, 0), 1]])
    run = run_checks(warehouse, NOW, {"ga4": 30.0, "stripe": 30.0}, sources={"stripe"}, write=False)
    assert [r.source for r in run.results] == ["stripe"] and warehouse.inserted == []


def test_ops_sql_has_the_table_and_the_definer_view() -> None:
    stmts = statements(OPS_SQL.read_text())
    assert len(stmts) == 2
    assert "CREATE TABLE IF NOT EXISTS ops.check_results" in stmts[0]
    columns = re.findall(r"^\s+(\w+) [\w()]+ COMMENT '", stmts[0], re.MULTILINE)
    assert tuple(columns) == CHECK_RESULT_COLUMNS
    assert "CREATE OR REPLACE VIEW ops.freshness" in stmts[1] and "SQL SECURITY DEFINER" in stmts[1]
    assert "WHERE check = 'freshness'" in stmts[1] and all(";" not in s for s in stmts)


def test_cli_exit_codes(monkeypatch: pytest.MonkeyPatch, warehouse: FakeWarehouse) -> None:
    warehouse.objects = {"raw.ga4___sync_windows"}
    warehouse.when("raw.ga4___sync_windows", [[datetime(2026, 10, 5, 3, 0), 1]])
    monkeypatch.setattr(cli, "pipeline_client", lambda: warehouse)

    class Config:
        @staticmethod
        def get(key: str, _type: Any) -> Any:
            return {"ga4": 1.0}

    monkeypatch.setattr(dlt, "config", Config())
    monkeypatch.setattr(
        cli,
        "run_checks",
        lambda client, budgets=None, sources=None: CheckRun(
            [CheckResult("ga4", "freshness", "sync", Status.PASS, 1, 2, "ok")]
        ),
    )
    assert cli.main(["check"]) == 0
    monkeypatch.setattr(
        cli,
        "run_checks",
        lambda client, budgets=None, sources=None: CheckRun(
            [CheckResult("ga4", "freshness", "sync", Status.FAIL, 99, 2, "stale")]
        ),
    )
    assert cli.main(["check", "--source", "ga4"]) == 1
    assert any("ops.check_results" in c for c in warehouse.commands)  # the ops SQL was applied first
