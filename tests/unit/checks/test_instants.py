"""Timestamps written to ``ops`` are instants: the same stored time from a host in any time zone."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest

from omw.checks import Status, run_checks
from omw.checks.results import CheckResult, write_results
from omw.pipelines.instants import instant, utc_wall_clock
from omw.pipelines.ops_log import PACING_EVENT_COLUMNS, PacingEvent
from tests.unit.checks.conftest import FakeWarehouse

ZONES = ["UTC", "Australia/Perth", "America/Los_Angeles"]


@pytest.fixture(params=ZONES)
def host_zone(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


def test_instant_takes_a_naive_time_as_utc_and_keeps_an_aware_one(host_zone: str) -> None:
    naive = datetime(2026, 10, 5, 4, 0)
    assert instant(naive) == datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
    perth = datetime(2026, 10, 5, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    assert instant(perth) == instant(naive) and instant(perth).tzinfo is UTC
    assert abs(instant().timestamp() - time.time()) < 5
    assert utc_wall_clock(perth) == naive and utc_wall_clock(naive) == naive


def test_a_check_result_is_written_at_the_instant_it_ran(host_zone: str) -> None:
    """``checked_at`` used to be UTC wall-clock with the zone stripped. The driver reads a naive time in the
    host's zone, so from Perth every verdict was stored eight hours in the past."""
    warehouse = FakeWarehouse()
    write_results(warehouse, [CheckResult("ga4", "freshness", "sync", Status.PASS, 1, 2, "ok")])
    written = warehouse.inserted[0][1][0][0]
    assert written.tzinfo is not None and abs(written.timestamp() - time.time()) < 5
    # A caller that still passes UTC wall-clock without a zone means that instant, wherever the host is.
    naive = CheckResult("ga4", "freshness", "sync", Status.PASS, 1, 2, "ok", datetime(2026, 10, 5, 4, 0))
    assert naive.row()[0].timestamp() == 1791172800.0


def test_a_pacing_event_is_written_at_the_instant_it_happened(host_zone: str) -> None:
    """``recorded_at`` decides whether an expired token still counts against freshness (the last 24 hours)."""
    event = PacingEvent("meta_ads", "1", "daily", "token_expired")
    row = dict(zip(PACING_EVENT_COLUMNS, event.row(), strict=True))
    assert row["recorded_at"].tzinfo is not None and abs(row["recorded_at"].timestamp() - time.time()) < 5
    assert row["source"] == "meta_ads" and row["event"] == "token_expired" and row["sleep_seconds"] == 0.0
    at = PacingEvent("meta_ads", "1", "daily", "stopped", recorded_at=datetime(2026, 10, 5, 4, 0))
    assert at.row()[0].timestamp() == 1791172800.0


def test_freshness_is_judged_the_same_whichever_zone_now_carries(host_zone: str) -> None:
    """The sync log comes back from the driver as naive UTC. An aware ``now`` in another zone is the same
    moment, so the age and the token window must not move with it."""
    naive = datetime(2026, 10, 5, 4, 0)
    aware = datetime(2026, 10, 5, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    runs = []
    for now in (naive, aware):
        warehouse = FakeWarehouse({"raw.ga4___sync_windows", "ops.pacing_events"})
        warehouse.when("raw.ga4___sync_windows", [[datetime(2026, 10, 5, 1, 0), 1]])
        warehouse.when("FROM ops.pacing_events", [])
        run = run_checks(warehouse, now, {"ga4": 30.0}, write=False, today=naive.date())
        token_query = next(sql for sql in warehouse.queries if "ops.pacing_events" in sql)
        runs.append((run.results[0].observed, run.results[0].status, token_query))
    assert runs[0] == runs[1]
    assert runs[0][:2] == (3.0, Status.PASS) and "recorded_at >= '2026-10-04 04:00:00'" in runs[0][2]
