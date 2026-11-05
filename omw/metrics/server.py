"""The ``query_metrics`` MCP server (DESIGN.md §6), on the official MCP Python SDK.

    uv run python -m omw.metrics.server            # stdio

Tools: ``list_metrics``, ``list_dimensions``, ``query_metrics``. Every answer carries a mood
(serve / disclose / clarify / refuse) with ``reason``, ``caveats`` and ``candidates`` as structured fields.
"""

from __future__ import annotations

import sys
from datetime import date
from functools import lru_cache
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from omw.metrics.moods import Answer
from omw.metrics.request import Compare, MetricRequest
from omw.metrics.runner import Executor, agent_client, answer_request, clickhouse_executor
from omw.metrics.schema import Catalog, load_catalog

SERVER_NAME = "query_metrics"


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    return load_catalog()


def list_metrics_payload(cat: Catalog) -> dict[str, Any]:
    metrics = [
        {
            "name": m.name,
            "source": m.source,
            "report": m.report,
            "unit": m.unit,
            "aggregation": m.aggregation,
            "grains": ["date", *m.grains],
            "aliases": m.aliases,
            "description": m.description,
        }
        for m in cat.metrics.values()
    ]
    derived = [
        {
            "name": d.name,
            "derived": d.derived,
            "unit": d.unit,
            "common_grain": d.requires_common_grain,
            "aliases": d.aliases,
            "description": d.description,
        }
        for d in cat.derived.values()
    ]
    return {"metrics": metrics, "derived": derived}


def list_dimensions_payload(cat: Catalog) -> dict[str, Any]:
    return {
        "reports": [
            {
                "report": key,
                "view": report.view,
                "description": report.description,
                "dimensions": dims,
                "restatement_days": report.restatement_days,
                "traps": report.traps,
            }
            for (key, report), dims in zip(
                cat.reports.items(), cat.dimension_descriptions().values(), strict=True
            )
        ]
    }


def query_metrics_payload(
    cat: Catalog,
    execute: Executor,
    metrics: list[str],
    time_range: dict[str, str],
    dimensions: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    compare: Compare | None = None,
    limit: int = 1000,
    today: date | None = None,
) -> dict[str, Any]:
    try:
        request = MetricRequest(
            metrics=metrics,
            dimensions=dimensions or [],
            filters=filters or {},
            time_range=time_range,  # type: ignore[arg-type]
            compare=compare,
            limit=limit,
        )
    except ValidationError as exc:
        return Answer.refuse(f"invalid request: {exc.errors()[0]['msg']}").to_dict()
    return answer_request(cat, request, execute, today).to_dict()


def build_server(execute: Executor | None = None) -> MCPServer:
    server = MCPServer(
        name=SERVER_NAME,
        instructions=(
            "Metrics over the marketing warehouse. Call list_metrics and list_dimensions first. "
            "query_metrics answers with a mood: serve (clean), disclose (read the caveats), clarify "
            "(pick one of the candidates), refuse (no valid path; the reason says why). Rates and "
            "cross-source measures are computed at query time; never sum them yourself."
        ),
    )
    executor = execute

    def _execute(sql: str) -> tuple[list[str], list[list[Any]]]:
        nonlocal executor
        if executor is None:
            executor = clickhouse_executor(agent_client())
        return executor(sql)

    @server.tool(description="Every metric and derived measure, with units, grains and aliases.")
    def list_metrics() -> dict[str, Any]:
        return list_metrics_payload(catalog())

    @server.tool(description="Every report grain with its dimensions, restatement window and known traps.")
    def list_dimensions() -> dict[str, Any]:
        return list_dimensions_payload(catalog())

    @server.tool(
        description=(
            "Query metrics at a grain. time_range is {start, end} as ISO dates; compare is one of "
            "previous_period, week, month, year and adds a 'period' column. Filters are dimension -> value "
            "or list of values. The answer carries mood, reason, caveats, candidates, sql, columns and rows."
        )
    )
    def query_metrics(
        metrics: list[str],
        time_range: dict[str, str],
        dimensions: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        compare: Compare | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        return query_metrics_payload(
            catalog(), _execute, metrics, time_range, dimensions, filters, compare, limit
        )

    return server


def main() -> int:
    build_server().run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
