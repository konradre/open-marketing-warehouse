"""Fixture mode for Meta Ads: account objects with the slice of the SDK's ``AdAccount`` the source uses.

The Meta source talks to the Marketing API through the ``facebook_business`` SDK, so its fixture seam is
the account object (``meta_ads(accounts=...)``), not the HTTP session. A fixture account lists the entities,
answers every async insights request with a report run that is already complete, and serves the leads of
an ad created after the cursor the source sends.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

from omw.demo.replay.transport import Fixtures
from omw.sources.meta_ads.settings import BREAKDOWNS

SOURCE = "meta_ads"
LEVEL_FILES = {"campaign": "insights_campaigns", "ad": "insights_ads"}


class FixtureJob:
    """An async report run that has finished: the source polls it once and reads the rows."""

    def __init__(self, job_id: str, rows: list[dict[str, Any]]) -> None:
        self._status = {"id": job_id, "async_status": "Job Completed", "async_percent_completion": 100}
        self._rows = rows

    def api_get(self) -> FixtureJob:
        return self

    def __getitem__(self, key: str) -> Any:
        return self._status[key]

    def get_result(self, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        return iter(self._rows)


class FixtureAccount:
    def __init__(self, fixtures: Fixtures, account_id: str) -> None:
        self._fixtures = fixtures
        self.account_id = account_id
        self.insight_requests = 0
        self._by_day: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def _rows(self, name: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = self._fixtures.load(SOURCE, name)["data"]
        return [row for row in rows if row.get("account_id", self.account_id) == self.account_id]

    def api_get(self, fields: Sequence[str]) -> dict[str, Any]:
        account: dict[str, Any] = self._fixtures.load(SOURCE, "account")
        return {"id": account["id"], **{name: account[name] for name in fields if name in account}}

    def _entities(self, name: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        states = params.get("effective_status")
        return iter([row for row in self._rows(name) if not states or row["effective_status"] in states])

    def get_campaigns(self, fields: Sequence[str], params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return self._entities("campaigns", params)

    def get_ad_sets(self, fields: Sequence[str], params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return self._entities("ad_sets", params)

    def get_ads(self, fields: Sequence[str], params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return self._entities("ads", params)

    def get_insights(self, params: dict[str, Any], is_async: bool = False) -> FixtureJob:
        """One report run for the day in ``time_range``, at the level or breakdown asked for."""
        self.insight_requests += 1
        if params.get("breakdowns"):
            report = next(name for name, fields in BREAKDOWNS.items() if list(fields) == params["breakdowns"])
            name = f"insights_{report}"
        else:
            name = LEVEL_FILES[params["level"]]
        if name not in self._by_day:
            index: dict[str, list[dict[str, Any]]] = {}
            for row in self._rows(name):
                index.setdefault(row["date_start"], []).append(row)
            self._by_day[name] = index
        day = params["time_range"]["since"]
        return FixtureJob(f"fixture-{name}-{day}", self._by_day[name].get(day, []))

    def leads_of(self, ad_id: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        since = int(params["filtering"][0]["value"])
        for row in self._rows("leads"):
            created = datetime.strptime(row["created_time"], "%Y-%m-%dT%H:%M:%S%z")
            if row["ad_id"] == ad_id and int(created.timestamp()) > since:
                yield row


def fixture_lead_fetcher(account: Any, ad_id: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The ``lead_fetcher`` of the Meta source in fixture mode."""
    rows: Iterator[dict[str, Any]] = account.leads_of(ad_id, params)
    return rows
