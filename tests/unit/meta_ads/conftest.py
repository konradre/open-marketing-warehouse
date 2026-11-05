"""Fake Meta SDK objects serving the recorded fixtures."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from omw.pacing import Decision, MetaPacer, StopReason
from omw.sources.meta_ads.settings import BREAKDOWNS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "meta_ads"
ACCOUNT = "100000000000001"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def fixture_rows(name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = fixture(name)["data"]
    return rows


class FakeJob:
    """An AdReportRun: ``api_get`` walks the recorded statuses, ``get_result`` pages the rows."""

    def __init__(self, rows: list[dict[str, Any]], statuses: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows
        # Completed at once by default: the recorded status walk (with its waits) is for the polling test.
        self._statuses = statuses or [fixture("job_statuses")[-1]]
        self._polls = 0
        self.result_params: dict[str, Any] | None = None

    def api_get(self) -> FakeJob:
        self._polls = min(self._polls + 1, len(self._statuses))
        return self

    def __getitem__(self, key: str) -> Any:
        return self._statuses[self._polls - 1][key]

    def get_result(self, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        self.result_params = params
        return iter(self._rows)


class FakeAccount:
    """An AdAccount: entity lists from fixtures, one insights job per requested day.

    ``stop_on`` makes the pacer stop (as the session would) when that day is requested, so a resource
    sees ``PacerStop`` in the middle of its window. ``restated`` replaces the rows of one day.
    """

    def __init__(
        self,
        account_id: str = ACCOUNT,
        currency: str = "USD",
        pacer: MetaPacer | None = None,
        stop_on: date | None = None,
        restated: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.account_id = account_id
        self.currency = currency
        self.pacer = pacer
        self.stop_on = stop_on
        self.restated = restated or {}
        self.insight_params: list[dict[str, Any]] = []
        self.entity_calls: list[tuple[str, list[str], dict[str, Any]]] = []
        self.lead_calls: list[tuple[str, dict[str, Any]]] = []
        self.currency_lookups = 0

    def api_get(self, fields: Sequence[str]) -> dict[str, Any]:
        self.currency_lookups += 1
        return {"id": f"act_{self.account_id}", "currency": self.currency}

    def _entities(self, name: str, fields: Sequence[str], params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.entity_calls.append((name, list(fields), dict(params)))
        return iter(fixture_rows(name))

    def get_campaigns(self, fields: Sequence[str], params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return self._entities("campaigns", fields, params)

    def get_ad_sets(self, fields: Sequence[str], params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return self._entities("ad_sets", fields, params)

    def get_ads(self, fields: Sequence[str], params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return self._entities("ads", fields, params)

    def get_leads(self, ad_id: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Leads of one ad created after the ``time_created`` filter, as the SDK would page them."""
        self.lead_calls.append((ad_id, dict(params)))
        since = int(params["filtering"][0]["value"])
        for row in fixture_rows("leads"):
            created = datetime.strptime(row["created_time"], "%Y-%m-%dT%H:%M:%S%z")
            if row["ad_id"] == ad_id and int(created.timestamp()) > since:
                yield row

    def get_insights(self, params: dict[str, Any], is_async: bool = False) -> FakeJob:
        assert is_async
        self.insight_params.append(params)
        day = params["time_range"]["since"]
        if self.stop_on is not None and day == self.stop_on.isoformat() and self.pacer is not None:
            self.pacer.apply(
                Decision(
                    96.0,
                    "x-fb-ads-insights-throttle:app_id_util_pct",
                    0.0,
                    StopReason.UTILISATION,
                    "simulated",
                )
            )
        if params.get("breakdowns"):
            report = next(n for n, f in BREAKDOWNS.items() if list(f) == params["breakdowns"])
            name = f"insights_{report}_{day}"
        else:
            name = f"insights_{params['level']}s_{day}"
        rows = self.restated.get(day) or (fixture_rows(name) if (FIXTURES / f"{name}.json").exists() else [])
        return FakeJob(rows)


@pytest.fixture
def pacer() -> MetaPacer:
    return MetaPacer(sleep=lambda _: None)


@pytest.fixture
def account(pacer: MetaPacer) -> FakeAccount:
    return FakeAccount(pacer=pacer)


def fake_lead_fetcher(account: Any, ad_id: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    rows: Iterator[dict[str, Any]] = account.get_leads(ad_id, params)
    return rows
