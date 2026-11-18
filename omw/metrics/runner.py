"""Run a compiled plan as the ``agent`` ClickHouse user and wrap the result in an ``Answer`` with a mood.

After the numbers, the runner reads the latest post-sync verdicts (``ops.check_verdicts``, DESIGN.md §7) for
the sources involved: a source past its freshness budget, or a failed over-count or row-count check on a
report the answer used, becomes a caveat so the answer is disclosed, never served as clean.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import date
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from omw.metrics.compiler import CAMPAIGN_KEYS_TABLE, Plan, compile_request
from omw.metrics.guard import SqlGuardError, assert_single_select
from omw.metrics.moods import Answer, ClarifyError, Mood, RefuseError
from omw.metrics.request import MetricRequest
from omw.metrics.schema import Catalog

Executor = Callable[[str], tuple[list[str], list[list[Any]]]]
"""Runs one guarded SELECT and returns (column names, rows)."""

VERDICTS_VIEW = "ops.check_verdicts"


def agent_client() -> Client:
    """clickhouse-connect as the ``agent`` user, from the same environment the tests and compose use."""
    return clickhouse_connect.get_client(
        host=os.environ.get("OMW_CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.environ.get("OMW_CLICKHOUSE_HTTP_PORT", "8123")),
        username="agent",
        password=os.environ.get("OMW_AGENT_PASSWORD", "agent-dev-password"),
        connect_timeout=5,
        send_receive_timeout=60,
    )


def clickhouse_executor(client: Client) -> Executor:
    def execute(sql: str) -> tuple[list[str], list[list[Any]]]:
        result = client.query(sql)
        return list(result.column_names), [list(row) for row in result.result_rows]

    return execute


def plan_request(catalog: Catalog, request: MetricRequest, today: date | None = None) -> Plan | Answer:
    """A plan, or the refuse/clarify answer when there is no valid path."""
    try:
        return compile_request(catalog, request, today)
    except ClarifyError as exc:
        return Answer.clarify(exc.reason, exc.candidates)
    except RefuseError as exc:
        return Answer.refuse(str(exc))


def verdicts_sql(sources: list[str]) -> str:
    """The latest non-passing verdict of every check on ``sources`` (names validated by the catalog)."""
    quoted = ", ".join(f"'{source}'" for source in sorted(set(sources)))
    return (
        f"SELECT source, check, report, status, detail FROM {VERDICTS_VIEW} "
        f"WHERE source IN ({quoted}) AND status != 'pass' ORDER BY source, check, report"
    )


def verdict_caveats(plan: Plan, execute: Executor) -> list[str]:
    """Caveats from the post-sync checks: stale sources, and failed checks on the reports the plan used.

    When the view is missing or unreadable (the checks have never run) there is nothing to disclose.
    """
    reports = {(p.report.source, p.report.name) for p in plan.reports}
    try:
        _, rows = execute(verdicts_sql([source for source, _ in reports]))
    except DatabaseError:
        return []
    found: list[str] = []
    for source, check, report, status, detail in rows:
        if check == "freshness":
            found.append(f"{source} freshness check {status}: {detail}; its numbers may be stale")
        elif (source, report) in reports:
            found.append(f"{source}.{report} {check} check {status}: {detail}")
    return found


UNKNOWN_TABLE = ("UNKNOWN_TABLE", "Unknown table expression identifier")
"""How ClickHouse says a table or view is not there (code 60), in its two analyzer generations."""


def refusal_reason(plan: Plan, exc: DatabaseError) -> str:
    """Why the warehouse would not run a compiled query, in the agent's words where the cause is known.

    A view that does not exist is the common one: every ``marts`` view is created by its pipeline after the
    first successful load, so a report the deployment never synced (ad-level insights, lead forms) has no
    view yet. That is a fact about the warehouse, not an error in the request.
    """
    text = str(exc)
    if any(marker in text for marker in UNKNOWN_TABLE):
        missing = [p.report for p in plan.reports if _names(text, p.report.view)]
        if missing:
            listed = ", ".join(f"{report.key} (view {report.view})" for report in missing)
            return (
                f"not loaded in this warehouse: {listed}. The source has not synced this report yet, so "
                "there is nothing to answer from"
            )
        if _names(text, CAMPAIGN_KEYS_TABLE):
            return (
                f"the campaign key mapping {CAMPAIGN_KEYS_TABLE} does not exist yet: it is created by the "
                "first ad platform sync, or by `python -m omw.metrics.setup`"
            )
    return f"the warehouse refused the query: {text[:300]}"


def _names(text: str, table: str) -> bool:
    """Whether an error names ``table`` itself, not a longer name that starts with it."""
    index = text.find(table)
    while index != -1:
        end = index + len(table)
        if end == len(text) or not (text[end].isalnum() or text[end] == "_"):
            return True
        index = text.find(table, end)
    return False


def answer_request(
    catalog: Catalog, request: MetricRequest, execute: Executor, today: date | None = None
) -> Answer:
    planned = plan_request(catalog, request, today)
    if isinstance(planned, Answer):
        return planned
    try:
        sql = assert_single_select(planned.sql)
    except SqlGuardError as exc:  # the compiler never emits this; the guard is the second line
        return Answer.refuse(f"compiled SQL rejected by the guard: {exc}")
    try:
        columns, rows = execute(sql)
    except DatabaseError as exc:
        return Answer.refuse(refusal_reason(planned, exc))
    truncated = len(rows) >= request.limit
    caveats = [*planned.caveats, *verdict_caveats(planned, execute)]
    if truncated:
        caveats.append(f"result cut at the limit of {request.limit} rows; narrow the request")
    mood = Mood.DISCLOSE if caveats else Mood.SERVE
    return Answer(
        mood=mood,
        caveats=caveats,
        comparison=planned.comparison,
        sql=sql,
        columns=columns,
        rows=rows,
        truncated=truncated,
    )
