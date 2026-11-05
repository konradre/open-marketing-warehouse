"""Recorded Stripe list responses and a fake client serving them."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from omw.sources.stripe.client import StripeClient

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "stripe"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def fixture_data(name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = fixture(name)["data"]
    return data


class FakeStripeClient(StripeClient):
    """Serves the recorded pages; ``overrides`` swaps the fixture a resource answers with."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.overrides: dict[str, str] = {}

    def list(self, resource: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        self.calls.append((resource, dict(params or {})))
        if resource == "customers":
            yield from fixture_data("customers_page_1") + fixture_data("customers_page_2")
            return
        name = self.overrides.get(resource, resource)
        rows = fixture_data(name)
        since = (params or {}).get("created[gte]")
        if since is not None:
            rows = [r for r in rows if r["created"] >= int(since)]
        yield from rows


@pytest.fixture
def fake_client() -> FakeStripeClient:
    return FakeStripeClient()
