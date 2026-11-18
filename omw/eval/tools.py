"""The two arms: what each one gives the agent to read and to call.

``sql``: the ``marts`` schema with its column comments, and ``run_query`` through mcp-clickhouse as the
``agent`` user. ``metrics``: ``SEMANTICS.md``, and the ``query_metrics`` server's three tools. A toolbox
records every call, so a run keeps the full trajectory behind each answer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from omw.eval.world import TODAY
from omw.metrics.guard import SqlGuardError, assert_single_select
from omw.metrics.runner import Executor, clickhouse_executor
from omw.metrics.schema import Catalog, load_catalog
from omw.metrics.server import list_dimensions_payload, list_metrics_payload, query_metrics_payload

SEMANTICS = Path(__file__).resolve().parents[2] / "SEMANTICS.md"
SCHEMA_SQL = (
    "SELECT database, table, name, type, comment FROM system.columns "
    "WHERE database IN ('marts', 'ops') ORDER BY database, table, position"
)
ARMS = ("sql", "metrics")


class ToolError(Exception):
    """A tool call that failed; the message is what the agent is shown."""


class InfraError(ToolError):
    """The plumbing failed, not the question: the agent's hourly quota ran out, or the warehouse or
    mcp-clickhouse could not be reached. A planner must let it through; the run records an ``error`` row
    instead of blaming the answer."""


INFRA_MARKERS = ("QUOTA_EXCEEDED", "unreachable", "Connection refused", "Max retries exceeded")


def raise_for(message: str) -> ToolError:
    """The error to raise for a failed call: ``InfraError`` when the failure is the plumbing's."""
    kind = InfraError if any(marker in message for marker in INFRA_MARKERS) else ToolError
    return kind(message)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments, "result": self.result, "error": self.error}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


class Toolbox(Protocol):
    arm: str
    calls: list[ToolCall]

    def context(self) -> str:
        """The document the agent is given beside its tools."""

    def specs(self) -> list[ToolSpec]: ...

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run a tool; raises ``ToolError`` when it fails. Recorded either way."""


SqlRunner = Callable[[str], dict[str, Any]]
"""Runs one query as the agent and returns ``{"columns": [...], "rows": [[...]]}``, or raises ToolError."""


def _plain(value: Any) -> Any:
    """A cell as mcp-clickhouse serialises it: decimals and dates as text."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def direct_runner(client: Any) -> SqlRunner:
    """clickhouse-connect as the ``agent`` user behind the single-SELECT guard: the same boundary without
    the MCP hop, for a stack that runs no mcp-clickhouse."""

    def run(query: str) -> dict[str, Any]:
        try:
            result = client.query(assert_single_select(query))
        except SqlGuardError as exc:
            raise ToolError(f"rejected: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - any warehouse error is the agent's to read
            raise raise_for(f"Query execution failed: {str(exc)[:400]}") from exc
        rows = [[_plain(cell) for cell in row] for row in result.result_rows]
        return {"columns": list(result.column_names), "rows": rows}

    return run


def mcp_runner(url: str, token: str) -> SqlRunner:
    """``run_query`` on mcp-clickhouse over streamable HTTP (the agent's real SQL path, DESIGN.md §2)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    async def call(query: str) -> dict[str, Any]:
        async with (
            create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http,
            streamable_http_client(url, http_client=http, max_sse_event_size=None) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("run_query", {"query": query})
        text = "\n".join(getattr(block, "text", "") for block in result.content)
        if result.is_error:
            raise raise_for(text[:600])
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolError(f"unreadable result: {text[:200]}") from exc
        if not isinstance(payload, dict) or "rows" not in payload:
            raise ToolError(f"unexpected result: {text[:200]}")
        return payload

    def run(query: str) -> dict[str, Any]:
        try:
            return asyncio.run(call(query))
        except ToolError:
            raise
        except BaseException as exc:  # noqa: BLE001 - connection failures arrive as exception groups
            if isinstance(exc, KeyboardInterrupt | SystemExit):
                raise
            raise InfraError(f"mcp-clickhouse unreachable at {url}: {exc!r}"[:400]) from exc

    return run


def cached(run: SqlRunner) -> SqlRunner:
    """Answer a query already run in this process from memory. The seeded warehouse does not change during
    a run and the planners repeat many queries, so this keeps a whole evaluation inside the ``agent``
    user's hourly quota (DESIGN.md §7) without touching the quota."""
    seen: dict[str, dict[str, Any]] = {}

    def lookup(query: str) -> dict[str, Any]:
        if query not in seen:
            seen[query] = run(query)
        return seen[query]

    return lookup


def cached_executor(execute: Executor) -> Executor:
    """The same, for the metric layer's executor (its compiled queries and its verdict lookups)."""
    seen: dict[str, tuple[list[str], list[list[Any]]]] = {}

    def lookup(sql: str) -> tuple[list[str], list[list[Any]]]:
        if sql not in seen:
            seen[sql] = execute(sql)
        return seen[sql]

    return lookup


def schema_text(run: SqlRunner) -> str:
    """Every table the agent can see with its columns, types and comments, read through its own tool."""
    lines: list[str] = []
    current = ""
    for database, table, name, kind, comment in run(SCHEMA_SQL)["rows"]:
        if f"{database}.{table}" != current:
            current = f"{database}.{table}"
            lines.append(f"\n{current}")
        lines.append(f"  {name} {kind}" + (f" -- {comment}" if comment else ""))
    return "\n".join(lines).strip()


@dataclass
class SqlToolbox:
    run: SqlRunner
    arm: str = "sql"
    calls: list[ToolCall] = field(default_factory=list)
    _schema: str = ""

    def context(self) -> str:
        if not self._schema:
            self._schema = schema_text(self.run)
        return (
            "You can read a ClickHouse marketing warehouse with run_query. These are its tables, with each "
            "column's type and comment:\n\n" + self._schema
        )

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "run_query",
                "Run one read-only ClickHouse SELECT as the agent user. Returns columns and rows.",
                {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "A single SELECT statement"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            )
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        record = ToolCall(name, arguments)
        self.calls.append(record)
        try:
            if name != "run_query":
                raise ToolError(f"unknown tool {name!r}")
            record.result = self.run(str(arguments.get("query", "")))
        except ToolError as exc:
            record.error = str(exc)
            raise
        return record.result


@dataclass
class MetricsToolbox:
    execute: Executor
    catalog: Catalog = field(default_factory=load_catalog)
    today: date = TODAY
    arm: str = "metrics"
    calls: list[ToolCall] = field(default_factory=list)

    def context(self) -> str:
        return SEMANTICS.read_text()

    def specs(self) -> list[ToolSpec]:
        empty = {"type": "object", "properties": {}, "additionalProperties": False}
        return [
            ToolSpec(
                "list_metrics", "Every metric and derived measure, with units, grains and aliases.", empty
            ),
            ToolSpec(
                "list_dimensions",
                "Every report grain with its dimensions, restatement window and known traps.",
                empty,
            ),
            ToolSpec(
                "query_metrics",
                "Query metrics at a grain. time_range is {start, end} as ISO dates or a named range "
                "(today, yesterday, last_7_days, last_30_days, last_90_days, month_to_date, last_month, "
                "year_to_date); every dated metric needs it, a snapshot metric has none. compare adds a "
                "'period' column: previous_period is the same number of days just before, week the same "
                "weekdays 7 days earlier, month and year the calendar period before. Filters are dimension "
                "-> value or list of values. The answer carries mood (serve, disclose, clarify, refuse), "
                "reason, caveats, candidates, comparison (the windows compared), sql, columns and rows.",
                {
                    "type": "object",
                    "properties": {
                        "metrics": {"type": "array", "items": {"type": "string"}},
                        "time_range": {
                            "anyOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
                                    "required": ["start", "end"],
                                },
                            ]
                        },
                        "dimensions": {"type": "array", "items": {"type": "string"}},
                        "filters": {"type": "object"},
                        "compare": {"type": "string", "enum": ["previous_period", "week", "month", "year"]},
                        "limit": {"type": "integer"},
                    },
                    "required": ["metrics"],
                },
            ),
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        record = ToolCall(name, arguments)
        self.calls.append(record)
        try:
            record.result = self._dispatch(name, arguments)
        except ToolError as exc:
            record.error = str(exc)
            raise
        return record.result

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "list_metrics":
            return list_metrics_payload(self.catalog)
        if name == "list_dimensions":
            return list_dimensions_payload(self.catalog)
        if name != "query_metrics":
            raise ToolError(f"unknown tool {name!r}")
        if "metrics" not in arguments:
            raise ToolError("query_metrics needs metrics")
        answer = query_metrics_payload(
            self.catalog,
            self.execute,
            arguments["metrics"],
            arguments.get("time_range"),
            arguments.get("dimensions"),
            arguments.get("filters"),
            arguments.get("compare"),
            int(arguments.get("limit", 1000)),
            today=self.today,
        )
        if answer["mood"] == "refuse" and any(marker in answer["reason"] for marker in INFRA_MARKERS):
            raise InfraError(answer["reason"])
        answer["rows"] = [[_plain(cell) for cell in row] for row in answer["rows"]]
        return answer


def metrics_toolbox(agent_client: Any) -> MetricsToolbox:
    return MetricsToolbox(cached_executor(clickhouse_executor(agent_client)))
