"""Recorded GA4 runReport responses and a fake client serving them."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from omw.sources.ga4.client import GA4Client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "ga4"
PROPERTY = "123456789"

_GRAIN_BY_DIMENSIONS = {
    ("date",): "core",
    ("date", "sessionDefaultChannelGroup"): "channels",
    ("date", "landingPage"): "landing_pages",
    ("date", "sessionCampaignName", "sessionSource", "sessionMedium"): "campaigns",
}


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def fixture_rows(name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = fixture(name).get("rows", [])
    return rows


class FakeGA4Client(GA4Client):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.restated: dict[str, list[dict[str, Any]]] = {}

    def run_report(
        self, property_id: str, start: date, end: date, dimensions: Sequence[str], metrics: Sequence[str]
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(
            {"property_id": property_id, "start": start, "end": end, "dimensions": list(dimensions)}
        )
        grain = _GRAIN_BY_DIMENSIONS[tuple(dimensions)]
        rows = fixture_rows(grain)
        if grain in self.restated:
            days = {r["dimensionValues"][0]["value"] for r in self.restated[grain]}
            rows = [r for r in rows if r["dimensionValues"][0]["value"] not in days] + self.restated[grain]
        for api_row in rows:
            value = api_row["dimensionValues"][0]["value"]
            day = date(int(value[:4]), int(value[4:6]), int(value[6:8]))
            if start <= day <= end:
                yield api_row


@pytest.fixture
def fake_client() -> FakeGA4Client:
    return FakeGA4Client()
