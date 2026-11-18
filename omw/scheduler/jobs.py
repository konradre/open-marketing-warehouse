"""The job table and the plan: which job runs when.

One job per source and cadence (DESIGN.md §5 for Meta's two; every other source re-fetches its own rolling
window once a day), then the backup and its restore drill, and an hourly run of every check. The defaults
are in UTC, early in the morning after the platforms have closed the previous day, and the jobs run one
after another in table order.

``[schedule]`` in ``.dlt/config.toml`` moves or disables a job::

    [schedule]
    meta_ads_weekly = "sat 02:00"
    linkedin_ads = "off"            # no LinkedIn account: not synced, not checked

A source whose jobs are all ``off`` is left out of the post-sync checks too, so one line says "we do not
use this source".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import dlt

from omw.scheduler.schedule import Schedule, ScheduleError, parse

Kind = Literal["sync", "backup", "backup_verify", "checks"]


@dataclass(frozen=True)
class Job:
    name: str
    kind: Kind
    default: str
    """Its schedule when ``[schedule]`` does not name it."""
    summary: str
    source: str = ""
    """The source a sync job loads; the post-sync checks of that source run after it."""
    module: str = ""
    """The pipeline module whose ``main`` runs the sync."""
    argv: tuple[str, ...] = ()
    covers: tuple[str, ...] = ()
    """Jobs this one makes redundant on the days it runs (the weekly Meta reconcile re-fetches the days the
    daily re-pull would)."""


JOBS: tuple[Job, ...] = (
    Job(
        "search_console",
        "sync",
        "03:00",
        "re-fetch the rolling 10 days",
        "search_console",
        "omw.pipelines.search_console",
        ("daily",),
    ),
    Job(
        "meta_ads_weekly",
        "sync",
        "sun 03:15",
        "28-day reconcile",
        "meta_ads",
        "omw.pipelines.meta_ads",
        ("weekly",),
        covers=("meta_ads_daily",),
    ),
    Job("meta_ads_daily", "sync", "03:15", "7-day re-pull", "meta_ads", "omw.pipelines.meta_ads", ("daily",)),
    Job(
        "google_ads",
        "sync",
        "04:00",
        "re-fetch the last 30 days",
        "google_ads",
        "omw.pipelines.google_ads",
        ("daily",),
    ),
    Job("ga4", "sync", "04:15", "re-fetch the last 7 days", "ga4", "omw.pipelines.ga4", ("daily",)),
    Job(
        "stripe",
        "sync",
        "04:30",
        "merge customers, subscriptions and recent invoices; append balance transactions",
        "stripe",
        "omw.pipelines.stripe",
        ("daily",),
    ),
    Job(
        "linkedin_ads",
        "sync",
        "04:45",
        "re-fetch the last 7 days",
        "linkedin_ads",
        "omw.pipelines.linkedin_ads",
        ("daily",),
    ),
    Job(
        "twenty",
        "sync",
        "05:00",
        "everything changed since the last run",
        "twenty",
        "omw.pipelines.twenty",
        ("daily",),
    ),
    Job("backup", "backup", "06:00", "back up raw, marts and ops; keep the newest few"),
    Job(
        "backup_verify",
        "backup_verify",
        "sun 06:30",
        "restore drill: the newest backup into scratch databases",
    ),
    Job(
        "checks",
        "checks",
        "*:50",
        "every post-sync check of every scheduled source, so one that stopped syncing turns stale",
    ),
)
JOB_NAMES = tuple(job.name for job in JOBS)
SOURCES = tuple(dict.fromkeys(job.source for job in JOBS if job.source))


@dataclass(frozen=True)
class Planned:
    job: Job
    schedule: Schedule
    """When it runs, with the days a covering job takes already removed."""


def job_named(name: str) -> Job:
    for job in JOBS:
        if job.name == name:
            return job
    raise ScheduleError(f"no job named {name!r}; the jobs are {', '.join(JOB_NAMES)}")


def build_plan(configured: Mapping[str, Any] | None = None) -> list[Planned]:
    """Every enabled job with its schedule, in table order. ``configured`` is the ``[schedule]`` table."""
    configured = dict(configured or {})
    unknown = sorted(set(configured) - set(JOB_NAMES))
    if unknown:
        raise ScheduleError(
            f"[schedule] names {', '.join(unknown)}, which is not a job; the jobs are {', '.join(JOB_NAMES)}"
        )
    schedules: dict[str, Schedule] = {}
    for job in JOBS:
        schedule = parse(str(configured.get(job.name, job.default)))
        if schedule is not None:
            schedules[job.name] = schedule
    for job in JOBS:
        for covered in job.covers:
            if job.name in schedules and covered in schedules:
                left = schedules[covered].without(schedules[job.name].weekdays)
                if left is None:
                    del schedules[covered]
                else:
                    schedules[covered] = left
    return [Planned(job, schedules[job.name]) for job in JOBS if job.name in schedules]


def configured_schedule() -> dict[str, Any]:
    """The ``[schedule]`` table of ``.dlt/config.toml`` (or ``SCHEDULE__<JOB>`` in the environment)."""
    table = dlt.config.get("schedule", dict) or {}
    found = {str(name): value for name, value in dict(table).items()}
    for name in JOB_NAMES:  # a table read from TOML misses jobs set through the environment only
        if name not in found:
            value = dlt.config.get(f"schedule.{name}", str)
            if value is not None:
                found[name] = value
    return found


def read_plan() -> list[Planned]:
    return build_plan(configured_schedule())


def enabled_sources(plan: list[Planned]) -> set[str]:
    """Sources with at least one scheduled job."""
    return {planned.job.source for planned in plan if planned.job.source}


def checked_sources(
    budgets: dict[str, float] | None, plan: list[Planned] | None = None
) -> tuple[dict[str, float] | None, set[str]]:
    """The freshness budgets and the sources the expiry check covers, without the sources whose jobs are all
    ``off`` in ``[schedule]``: a source the deployment does not use is neither synced nor checked."""
    enabled = enabled_sources(read_plan() if plan is None else plan)
    if budgets is not None:
        budgets = {source: hours for source, hours in budgets.items() if source in enabled}
    return budgets, set(budgets) if budgets is not None else enabled & set(SOURCES)
