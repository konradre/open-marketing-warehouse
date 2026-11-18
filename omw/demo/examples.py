"""Five questions for ``query_metrics``, chosen so the four answer moods all show up on the demo data.

Each one is the tool call an agent would make. They go through ``omw.metrics.server.query_metrics_payload``
as the ``agent`` ClickHouse user, exactly as the MCP server answers them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from omw.metrics.moods import Mood
from omw.metrics.runner import Executor
from omw.metrics.schema import Catalog
from omw.metrics.server import query_metrics_payload

MAX_ROWS = 8


@dataclass(frozen=True)
class Example:
    question: str
    shows: str
    """What the answer demonstrates, in one line."""
    expect: Mood
    metrics: list[str]
    time_range: dict[str, str] | str | None = None
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    caveat: str = ""
    """Text one of the answer's caveats must contain, when the example is about a particular caveat."""

    @property
    def call(self) -> str:
        """The tool call as an agent would write it."""
        arguments: dict[str, Any] = {"metrics": self.metrics}
        if self.dimensions:
            arguments["dimensions"] = self.dimensions
        if self.filters:
            arguments["filters"] = self.filters
        if self.time_range is not None:
            arguments["time_range"] = self.time_range
        return f"query_metrics({json.dumps(arguments)})"


def examples(today: date) -> list[Example]:
    settled = {"start": str(today - timedelta(days=34)), "end": str(today - timedelta(days=5))}
    return [
        Example(
            "How many organic search clicks did each device bring over 30 settled days?",
            "one source, one grain, a window Search Console has stopped revising",
            Mood.SERVE,
            ["search_device_clicks", "search_device_impressions"],
            settled,
            ["device"],
        ),
        Example(
            "What did each Meta campaign spend over the last 7 days, in dollars?",
            "the numbers come with the caveat that Meta is still restating these days",
            Mood.DISCLOSE,
            ["meta_spend", "meta_clicks"],
            "last_7_days",
            ["campaign_key"],
            {"currency": "USD"},
        ),
        Example(
            "What is the return on ad spend per campaign over the last 30 days, in dollars?",
            "Meta, Google Ads and LinkedIn spend joined to Stripe revenue on the shared campaign "
            "key, each source aggregated on its own first",
            Mood.DISCLOSE,
            ["ad_spend", "revenue", "roas"],
            "last_30_days",
            ["campaign_key"],
            {"currency": "USD"},
        ),
        Example(
            "What was our spend last week?",
            "'spend' names a metric on three platforms, so the layer asks which one instead of guessing",
            Mood.CLARIFY,
            ["spend"],
            "last_7_days",
        ),
        Example(
            "How many people did our Meta ads reach in total over the last 7 days?",
            "reach is deduplicated per campaign and day; adding it up would count people twice",
            Mood.REFUSE,
            ["meta_reach"],
            "last_7_days",
        ),
    ]


FAULT_EXAMPLES: dict[str, Example] = {
    "overcount": Example(
        "How many sessions did each channel bring over the last 7 days?",
        "the over-count check failed on this report after the sync, and the answer says so",
        Mood.DISCLOSE,
        ["channel_sessions"],
        "last_7_days",
        ["channel_group"],
        caveat="ga4.channels overcount check fail",
    ),
}
"""fault -> the question whose answer must carry the failed check as a caveat."""


def problem(example: Example, answer: dict[str, Any]) -> str:
    """Why an answer is not the one the example is there to show; empty when it is."""
    if answer["mood"] != example.expect:
        return (
            f"{example.metrics}: expected {example.expect}, got {answer['mood']} ({answer.get('reason', '')})"
        )
    if example.caveat and not any(example.caveat in caveat for caveat in answer.get("caveats", [])):
        return f"{example.metrics}: no caveat mentions {example.caveat!r}"
    return ""


def ask(example: Example, catalog: Catalog, execute: Executor, today: date) -> dict[str, Any]:
    return query_metrics_payload(
        catalog,
        execute,
        example.metrics,
        example.time_range,
        example.dimensions,
        example.filters,
        today=today,
    )


def _cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    if value is None:
        return "-"
    text = str(value)
    try:
        return f"{float(text):,.2f}" if "." in text else text
    except ValueError:
        return text


def table(columns: list[str], rows: list[list[Any]], indent: str = "  ") -> list[str]:
    """Rows as aligned text columns."""
    cells = [[_cell(value) for value in row] for row in rows]
    widths = [max(len(str(name)), *(len(row[n]) for row in cells)) for n, name in enumerate(columns)]
    lines = [indent + "  ".join(str(name).ljust(width) for name, width in zip(columns, widths, strict=True))]
    lines += [
        indent + "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in cells
    ]
    return [line.rstrip() for line in lines]


def render(number: int, example: Example, answer: dict[str, Any], total: int = 5) -> str:
    """One answer as the demo prints it: the question, the tool call, the mood and what came with it."""
    mood = answer["mood"]
    lines = [
        "",
        f"[{number}/{total}] {example.question}",
        f"  {example.call}",
        f"  mood: {mood}   ({example.shows})",
    ]
    if answer.get("reason"):
        lines.append(f"  reason: {answer['reason']}")
    for candidate in answer.get("candidates", []):
        lines.append(f"    - {candidate['name']}: {candidate['description']}")
    for caveat in answer.get("caveats", []):
        lines.append(f"  caveat: {caveat}")
    rows = answer.get("rows", [])
    if rows:
        lines += table(answer["columns"], rows[:MAX_ROWS], indent="    ")
        if len(rows) > MAX_ROWS:
            lines.append(f"    ... {len(rows) - MAX_ROWS} more rows")
    return "\n".join(lines)
