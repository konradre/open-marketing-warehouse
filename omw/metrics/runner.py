"""Run a compiled plan as the ``agent`` ClickHouse user and wrap the result in an ``Answer`` with a mood."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import date
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from omw.metrics.compiler import Plan, compile_request
from omw.metrics.guard import SqlGuardError, assert_single_select
from omw.metrics.moods import Answer, ClarifyError, Mood, RefuseError
from omw.metrics.request import MetricRequest
from omw.metrics.schema import Catalog

Executor = Callable[[str], tuple[list[str], list[list[Any]]]]
"""Runs one guarded SELECT and returns (column names, rows)."""


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
        return Answer.refuse(f"the warehouse refused the query: {str(exc)[:300]}")
    truncated = len(rows) >= request.limit
    caveats = list(planned.caveats)
    if truncated:
        caveats.append(f"result cut at the limit of {request.limit} rows; narrow the request")
    mood = Mood.DISCLOSE if caveats else Mood.SERVE
    return Answer(mood=mood, caveats=caveats, sql=sql, columns=columns, rows=rows, truncated=truncated)
