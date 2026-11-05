"""Paging entity lists and running async insights jobs. Re-authored from dlt's facebook_ads helpers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


class InsightsJobError(RuntimeError):
    pass


def export(item: Any) -> dict[str, Any]:
    """A plain dict from an SDK object (or a dict already, in tests)."""
    if isinstance(item, dict) and not hasattr(item, "export_all_data"):
        return dict(item)
    data: dict[str, Any] = item.export_all_data()
    return data


def page_entities(
    method: Callable[..., Any], fields: Sequence[str], states: Sequence[str] | None, page_size: int
) -> Iterator[list[dict[str, Any]]]:
    """Lists of ``page_size`` exported objects from a paging SDK method."""
    params: dict[str, Any] = {"limit": page_size}
    if states:
        params["effective_status"] = list(states)
    page: list[dict[str, Any]] = []
    for item in method(fields=list(fields), params=params):
        page.append(export(item))
        if len(page) >= page_size:
            yield page
            page = []
    if page:
        yield page


@dataclass(frozen=True)
class JobPolling:
    first_wait_seconds: float = 5.0
    max_wait_seconds: float = 60.0
    max_wait_to_start_seconds: float = 300.0
    max_wait_to_finish_seconds: float = 1800.0


DEFAULT_POLLING = JobPolling()
COMPLETED = "Job Completed"
FAILED_STATUSES = ("Job Failed", "Job Skipped")


def wait_for_job(
    job: Any,
    polling: JobPolling = DEFAULT_POLLING,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """Poll an async report run until it completes; raise on failure or when it never starts/finishes."""
    started = clock()
    wait = polling.first_wait_seconds
    while True:
        job = job.api_get()
        status = job["async_status"]
        percent = job["async_percent_completion"]
        if status == COMPLETED:
            return job
        if status in FAILED_STATUSES:
            raise InsightsJobError(f"insights job {job['id']} ended with status {status!r}")
        elapsed = clock() - started
        if percent == 0 and elapsed > polling.max_wait_to_start_seconds:
            raise InsightsJobError(f"insights job {job['id']} did not start after {elapsed:.0f}s")
        if elapsed > polling.max_wait_to_finish_seconds:
            raise InsightsJobError(f"insights job {job['id']} did not finish after {elapsed:.0f}s")
        log.debug("insights job %s: %s, %s%%; waiting %.0fs", job["id"], status, percent, wait)
        sleep(wait)
        wait = min(wait * 2, polling.max_wait_seconds)
