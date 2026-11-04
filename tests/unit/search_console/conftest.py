"""Recorded fixtures and a fake client for the Search Console tests."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from omw.sources.search_console.client import SearchConsoleClient

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "search_console"
SITE = "sc-domain:example.test"


def fixture_rows(name: str) -> list[dict[str, Any]]:
    payload = json.loads((FIXTURES / f"{name}.json").read_text())
    rows: list[dict[str, Any]] = payload.get("rows", [])
    return rows


class FakeSearchConsoleClient(SearchConsoleClient):
    """Serves the recorded grain fixtures, filtered to the requested window; records every call.

    ``restated`` maps a grain to rows that replace the fixture rows for the same day. ``fail_windows``
    makes a query raise for a window (to test a failed backfill chunk).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.restated: dict[str, list[dict[str, Any]]] = {}
        self.fail_windows: set[tuple[date, date]] = set()

    def query(
        self,
        site_url: str,
        start: date,
        end: date,
        dimensions: Sequence[str],
        search_type: str = "web",
        data_state: str = "all",
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(
            {
                "site_url": site_url,
                "start": start,
                "end": end,
                "dimensions": list(dimensions),
                "search_type": search_type,
                "data_state": data_state,
            }
        )
        if (start, end) in self.fail_windows:
            raise ConnectionError(f"simulated outage for {start}..{end}")
        grain = _grain_for(dimensions)
        rows = fixture_rows(grain)
        if grain in self.restated:
            restated_days = {row["keys"][0] for row in self.restated[grain]}
            rows = [row for row in rows if row["keys"][0] not in restated_days] + self.restated[grain]
        for row in rows:
            if start <= date.fromisoformat(row["keys"][0]) <= end:
                yield row


def _grain_for(dimensions: Sequence[str]) -> str:
    extra = tuple(dimensions[1:])
    return {
        (): "core",
        ("query",): "queries",
        ("page",): "pages",
        ("query", "page"): "query_page",
        ("country",): "countries",
        ("device",): "devices",
    }[extra]


@pytest.fixture
def fake_client() -> FakeSearchConsoleClient:
    return FakeSearchConsoleClient()
