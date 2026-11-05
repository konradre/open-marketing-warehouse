"""Recorded searchStream responses and a fake client serving them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from omw.sources.google_ads.client import GoogleAdsClient

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "google_ads"
CUSTOMER = "1234567890"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def fixture_results(name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chunk in fixture(name):
        rows.extend(chunk.get("results", []))
    return rows


class FakeGoogleAdsClient(GoogleAdsClient):
    """Answers each query from the fixture its FROM clause names, filtered to the requested window.

    ``restated`` replaces one day's campaign rows; ``fail_customers`` raises for a customer.
    """

    def __init__(self) -> None:
        self.queries: list[tuple[str, str]] = []
        self.restated: dict[str, list[dict[str, Any]]] = {}
        self.fail_customers: set[str] = set()

    def search_stream(self, customer_id: str, query: str) -> list[dict[str, Any]]:
        self.queries.append((customer_id, query))
        if customer_id in self.fail_customers:
            raise ConnectionError(f"simulated outage for {customer_id}")
        if "segments.date" not in query:
            return fixture_results("campaign_entities")
        if "FROM search_term_view" in query:
            name = "search_terms_stream"
        elif "FROM ad_group" in query:
            name = "ad_groups_stream"
        else:
            name = "campaigns_stream"
        start, end = _window(query)
        rows = fixture_results(name)
        if name == "campaigns_stream" and self.restated:
            rows = [r for r in rows if r["segments"]["date"] not in self.restated]
            for day_rows in self.restated.values():
                rows.extend(day_rows)
        return [r for r in rows if start <= r["segments"]["date"] <= end]


def _window(query: str) -> tuple[str, str]:
    marker = "segments.date BETWEEN '"
    start = query.index(marker) + len(marker)
    return query[start : start + 10], query[start + 17 : start + 27]


@pytest.fixture
def fake_client() -> FakeGoogleAdsClient:
    return FakeGoogleAdsClient()
