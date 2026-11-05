"""The MCP server: tool registration and the payloads the tools return."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from omw.metrics import Catalog
from omw.metrics.server import (
    build_server,
    list_dimensions_payload,
    list_metrics_payload,
    query_metrics_payload,
)
from tests.unit.metrics.conftest import TODAY


def _execute(sql: str) -> tuple[list[str], list[list[Any]]]:
    if "ops.check_verdicts" in sql:
        return ["source", "check", "report", "status", "detail"], []
    return ["date", "sessions"], [["2026-10-01", 10]]


def test_list_payloads(catalog: Catalog) -> None:
    metrics = list_metrics_payload(catalog)
    names = {m["name"] for m in metrics["metrics"]}
    assert {"meta_spend", "revenue"} <= names and {"roas", "cac"} <= {d["name"] for d in metrics["derived"]}
    spend = next(m for m in metrics["metrics"] if m["name"] == "meta_spend")
    assert spend["grains"][0] == "date" and "spend" in spend["aliases"] and spend["unit"] == "currency"
    dims = list_dimensions_payload(catalog)
    meta = next(r for r in dims["reports"] if r["report"] == "meta_ads.campaigns")
    assert meta["restatement_days"] == 28 and "campaign_key" in meta["dimensions"] and meta["traps"]


def test_query_payload_rejects_a_bad_request_as_refuse(catalog: Catalog) -> None:
    payload = query_metrics_payload(
        catalog, _execute, ["sessions"], {"start": "2026-10-03", "end": "2026-10-01"}, today=TODAY
    )
    assert payload["mood"] == "refuse" and "invalid request" in payload["reason"]
    payload = query_metrics_payload(
        catalog, _execute, [], {"start": "2026-10-01", "end": "2026-10-03"}, today=TODAY
    )
    assert payload["mood"] == "refuse"


def test_query_payload_serves(catalog: Catalog) -> None:
    payload = query_metrics_payload(
        catalog,
        _execute,
        ["sessions"],
        {"start": "2026-01-01", "end": "2026-01-31"},
        dimensions=["date"],
        today=TODAY,
    )
    assert (
        payload["mood"] == "serve"
        and payload["rows"] == [["2026-10-01", 10]]
        and payload["columns"] == ["date", "sessions"]
    )


def test_query_payload_takes_a_named_time_range(catalog: Catalog) -> None:
    payload = query_metrics_payload(
        catalog, _execute, ["sessions"], "last_7_days", dimensions=["date"], today=TODAY
    )
    assert payload["mood"] == "disclose"  # the last 3 days are inside GA4's restatement window
    assert "date BETWEEN '2026-09-28' AND '2026-10-04'" in payload["sql"]
    unknown = query_metrics_payload(catalog, _execute, ["sessions"], "last_fortnight", today=TODAY)
    assert unknown["mood"] == "refuse" and "invalid request" in unknown["reason"]


def test_server_registers_the_three_tools_and_runs_query_metrics() -> None:
    server = build_server(execute=_execute)
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {"list_metrics", "list_dimensions", "query_metrics"}
    query = next(tool for tool in tools if tool.name == "query_metrics")
    assert {"metrics", "time_range", "dimensions", "filters", "compare", "limit"} <= set(
        query.input_schema["properties"]
    )
    result = asyncio.run(
        server.call_tool(
            "query_metrics",
            {
                "metrics": ["sessions"],
                "time_range": {"start": "2026-01-01", "end": "2026-01-31"},
                "dimensions": ["date"],
            },
        )
    )
    payload = result.structured_content or json.loads(result.content[0].text)  # type: ignore[union-attr]
    assert isinstance(payload, dict) and payload["mood"] == "serve"
