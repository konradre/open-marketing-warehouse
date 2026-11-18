"""The two arms' toolboxes: what they expose, what they record, and which failures are the plumbing's."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from omw.eval import connect
from omw.eval.tools import (
    InfraError,
    MetricsToolbox,
    SqlToolbox,
    ToolError,
    cached,
    cached_executor,
    direct_runner,
    raise_for,
    schema_text,
)
from omw.eval.world import TODAY

VERDICT_COLUMNS = ["source", "check", "report", "status", "detail"]


class FakeResult:
    def __init__(self, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        self.column_names = columns
        self.result_rows = rows


class FakeClient:
    def __init__(self, result: FakeResult | Exception) -> None:
        self.result = result
        self.queries: list[str] = []

    def query(self, sql: str) -> FakeResult:
        self.queries.append(sql)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_the_direct_runner_serialises_cells_like_mcp_clickhouse() -> None:
    client = FakeClient(FakeResult(["d", "s", "n"], [(date(2026, 9, 1), Decimal("12.340000000"), 3)]))
    result = direct_runner(client)("SELECT d, s, n FROM marts.t;")
    assert result == {"columns": ["d", "s", "n"], "rows": [["2026-09-01", "12.340000000", 3]]}
    assert client.queries == ["SELECT d, s, n FROM marts.t"]


@pytest.mark.parametrize(
    "query", ["INSERT INTO marts.t VALUES (1)", "SELECT 1; SELECT 2", "SELECT 1 SETTINGS readonly = 0", ""]
)
def test_the_direct_runner_keeps_the_single_select_guard(query: str) -> None:
    client = FakeClient(FakeResult([], []))
    with pytest.raises(ToolError, match="rejected"):
        direct_runner(client)(query)
    assert client.queries == []


def test_a_warehouse_error_is_the_agents_to_read_and_a_quota_error_is_not() -> None:
    with pytest.raises(ToolError, match="UNKNOWN_IDENTIFIER") as info:
        direct_runner(FakeClient(RuntimeError("Code: 47. UNKNOWN_IDENTIFIER nope")))("SELECT nope")
    assert not isinstance(info.value, InfraError)
    with pytest.raises(InfraError):
        direct_runner(FakeClient(RuntimeError("Code: 201. QUOTA_EXCEEDED queries = 1001/1000")))("SELECT 1")
    assert isinstance(raise_for("Connection refused"), InfraError)
    assert type(raise_for("ACCESS_DENIED")) is ToolError


def test_settings_come_from_the_environment_then_dotenv_then_the_dev_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nOMW_MCP_AUTH_TOKEN='from-file'\nOMW_CLICKHOUSE_HOST=db.internal.example\n\nnoise\n"
    )
    assert connect.dotenv(env) == {
        "OMW_MCP_AUTH_TOKEN": "from-file",
        "OMW_CLICKHOUSE_HOST": "db.internal.example",
    }
    assert connect.dotenv(tmp_path / "missing") == {}
    values = connect.dotenv(env)
    monkeypatch.setattr(connect, "dotenv", lambda: values)
    monkeypatch.delenv("OMW_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("OMW_MCP_URL", "http://127.0.0.1:9999/mcp")
    assert connect.mcp_endpoint() == ("http://127.0.0.1:9999/mcp", "from-file")
    assert connect.setting("OMW_AGENT_PASSWORD", "agent-dev-password") == "agent-dev-password"


def test_identical_queries_run_once() -> None:
    seen: list[str] = []

    def run(query: str) -> dict[str, Any]:
        seen.append(query)
        return {"columns": ["n"], "rows": [[len(seen)]]}

    lookup = cached(run)
    assert lookup("SELECT 1") == lookup("SELECT 1") and lookup("SELECT 2")["rows"] == [[2]]
    assert seen == ["SELECT 1", "SELECT 2"]

    def execute(sql: str) -> tuple[list[str], list[list[Any]]]:
        seen.append(sql)
        return ["n"], [[1]]

    executor = cached_executor(execute)
    executor("SELECT 3")
    executor("SELECT 3")
    assert seen.count("SELECT 3") == 1


def test_a_failed_query_is_not_remembered() -> None:
    attempts = 0

    def run(query: str) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ToolError("first try fails")
        return {"columns": [], "rows": []}

    lookup = cached(run)
    with pytest.raises(ToolError):
        lookup("SELECT 1")
    assert lookup("SELECT 1") == {"columns": [], "rows": []}


def test_the_sql_arm_is_given_the_schema_with_its_comments_and_one_tool() -> None:
    rows = [
        ["marts", "meta_ads_campaigns", "spend", "Decimal(38, 9)", "Amount spent in the account currency"],
        ["marts", "meta_ads_campaigns", "ctr", "Float64", "A rate: never sum it"],
        ["ops", "freshness", "status", "LowCardinality(String)", ""],
    ]
    queries: list[str] = []

    def run(query: str) -> dict[str, Any]:
        queries.append(query)
        return {"columns": [], "rows": rows}

    text = schema_text(run)
    assert "marts.meta_ads_campaigns\n  spend Decimal(38, 9) -- Amount spent" in text
    assert "ops.freshness\n  status LowCardinality(String)" in text and "system.columns" in queries[0]
    box = SqlToolbox(run)
    assert "never sum it" in box.context() and box.context() == box.context() and len(queries) == 2
    assert [spec.name for spec in box.specs()] == ["run_query"] and box.arm == "sql"
    assert box.call("run_query", {"query": "SELECT 1"})["rows"] == rows
    with pytest.raises(ToolError, match="unknown tool"):
        box.call("drop_table", {})
    assert [(call.name, bool(call.error)) for call in box.calls] == [
        ("run_query", False),
        ("drop_table", True),
    ]


def _executor(sql: str) -> tuple[list[str], list[list[Any]]]:
    if "ops.check_verdicts" in sql:
        return VERDICT_COLUMNS, [["ga4", "freshness", "sync", "fail", "last sync 80.0h ago, budget 30h"]]
    return ["sessions"], [[Decimal("12")]]


def test_the_metric_arm_is_given_semantics_and_the_servers_three_tools() -> None:
    box = MetricsToolbox(_executor)
    assert box.context().startswith("# Semantics for the agent") and box.today == TODAY
    assert [spec.name for spec in box.specs()] == ["list_metrics", "list_dimensions", "query_metrics"]
    assert "meta_spend" in {m["name"] for m in box.call("list_metrics", {})["metrics"]}
    assert box.call("list_dimensions", {})["reports"]
    answer = box.call("query_metrics", {"metrics": ["sessions"], "time_range": "last_7_days"})
    assert answer["mood"] == "disclose" and answer["rows"] == [["12"]]
    assert "date BETWEEN '2026-09-24' AND '2026-09-30'" in answer["sql"]  # named ranges resolve on TODAY
    assert any("stale" in caveat for caveat in answer["caveats"])
    refused = box.call("query_metrics", {"metrics": ["users"], "time_range": "last_month"})
    assert refused["mood"] == "refuse" and "not additive" in refused["reason"]
    asked = box.call("query_metrics", {"metrics": ["clicks"], "time_range": "last_month"})
    assert asked["mood"] == "clarify" and len(asked["candidates"]) >= 4
    with pytest.raises(ToolError, match="needs metrics"):
        box.call("query_metrics", {"metrics": ["sessions"]})
    with pytest.raises(ToolError, match="unknown tool"):
        box.call("run_query", {"query": "SELECT 1"})
    assert len(box.calls) == 7 and box.calls[-1].error


def test_a_quota_failure_inside_the_metric_layer_is_plumbing_not_a_refusal() -> None:
    from clickhouse_connect.driver.exceptions import DatabaseError

    def exhausted(sql: str) -> tuple[list[str], list[list[Any]]]:
        raise DatabaseError("Code: 201. DB::Exception: Quota for user `agent` exceeded. (QUOTA_EXCEEDED)")

    with pytest.raises(InfraError, match="QUOTA_EXCEEDED"):
        MetricsToolbox(exhausted).call("query_metrics", {"metrics": ["sessions"], "time_range": "last_month"})
