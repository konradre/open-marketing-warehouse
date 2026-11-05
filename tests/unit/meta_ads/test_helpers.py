from __future__ import annotations

from typing import Any

import pytest

from omw.sources.meta_ads.helpers import InsightsJobError, JobPolling, page_entities, wait_for_job
from tests.unit.meta_ads.conftest import FakeJob, fixture, fixture_rows


def test_page_entities_chunks_and_passes_fields_and_states() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def method(fields: list[str], params: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append((fields, params))
        return fixture_rows("ads")

    pages = list(page_entities(method, ("id", "name"), ("ACTIVE", "PAUSED"), page_size=2))
    assert [len(page) for page in pages] == [2, 1]
    assert calls == [(["id", "name"], {"limit": 2, "effective_status": ["ACTIVE", "PAUSED"]})]
    assert pages[0][0]["id"] == "23870000000000001"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def test_wait_for_job_polls_with_doubling_waits_until_completed() -> None:
    clock = Clock()
    job = wait_for_job(
        FakeJob([], fixture("job_statuses")),
        JobPolling(first_wait_seconds=5, max_wait_seconds=8),
        clock.sleep,
        clock,
    )
    assert job["async_status"] == "Job Completed"
    assert clock.sleeps == [5.0, 8.0]


def test_wait_for_job_raises_on_a_failed_job() -> None:
    statuses = [{"id": "1", "async_status": "Job Failed", "async_percent_completion": 0}]
    with pytest.raises(InsightsJobError, match="Job Failed"):
        wait_for_job(FakeJob([], statuses), JobPolling(), lambda _: None, lambda: 0.0)


def test_wait_for_job_gives_up_when_the_job_never_starts() -> None:
    statuses = [{"id": "1", "async_status": "Job Not Started", "async_percent_completion": 0}] * 100
    clock = Clock()
    polling = JobPolling(first_wait_seconds=10, max_wait_seconds=10, max_wait_to_start_seconds=25)
    with pytest.raises(InsightsJobError, match="did not start"):
        wait_for_job(FakeJob([], statuses), polling, clock.sleep, clock)
    assert clock.now == 30.0
