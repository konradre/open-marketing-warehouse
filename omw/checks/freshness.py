"""Freshness: each source's last successful sync (the ``sync_windows`` log every pipeline writes) must be
within its budget. A missing log, a dead Meta token or a stop recorded by the pacer counts against it."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from omw.checks.results import CheckResult, Status, Warehouse

DEFAULT_BUDGET_HOURS: dict[str, float] = {
    "search_console": 48.0,  # Google lags 2-3 days anyway; one missed daily run is tolerable
    "meta_ads": 30.0,
    "google_ads": 30.0,
    "ga4": 30.0,
    "stripe": 30.0,
    "linkedin_ads": 30.0,
}

SYNC_LOG = "raw.{source}___sync_windows"
PACING_EVENTS = "ops.pacing_events"


def last_sync_sql(source: str) -> str:
    return f"SELECT max(loaded_at), count() FROM {SYNC_LOG.format(source=source)}"


def token_events_sql(since: datetime) -> str:
    return (
        f"SELECT source, event, detail FROM {PACING_EVENTS} "
        f"WHERE event = 'token_expired' AND recorded_at >= '{since:%Y-%m-%d %H:%M:%S}'"
    )


def freshness_checks(
    client: Warehouse,
    now: datetime,
    budgets: dict[str, float] | None = None,
    existing_tables: set[str] | None = None,
) -> list[CheckResult]:
    budgets = budgets or DEFAULT_BUDGET_HOURS
    expired: dict[str, str] = {}
    if existing_tables is None or PACING_EVENTS in existing_tables:
        for source, _event, detail in client.query(token_events_sql(now - timedelta(days=1))).result_rows:
            expired[str(source)] = str(detail)
    results: list[CheckResult] = []
    for source, budget in budgets.items():
        table = SYNC_LOG.format(source=source)
        if existing_tables is not None and table not in existing_tables:
            results.append(
                CheckResult(
                    source, "freshness", "sync", Status.FAIL, 0.0, budget, "never synced: no sync log"
                )
            )
            continue
        last, runs = client.query(last_sync_sql(source)).result_rows[0]
        if not runs or last is None:
            results.append(
                CheckResult(
                    source, "freshness", "sync", Status.FAIL, 0.0, budget, "never synced: empty sync log"
                )
            )
            continue
        last_at: datetime = last.replace(tzinfo=None) if isinstance(last, datetime) else last
        age_hours = (now.replace(tzinfo=None) - last_at).total_seconds() / 3600
        if source in expired:
            status, detail = Status.FAIL, f"token expired: {expired[source]}"
        elif age_hours > budget:
            status, detail = Status.FAIL, f"last sync {age_hours:.1f}h ago, budget {budget:.0f}h"
        else:
            status, detail = Status.PASS, f"last sync {age_hours:.1f}h ago, budget {budget:.0f}h"
        results.append(CheckResult(source, "freshness", "sync", status, round(age_hours, 2), budget, detail))
    return results


def freshness_row(result: CheckResult) -> dict[str, Any]:
    return {
        "source": result.source,
        "age_hours": result.observed,
        "budget_hours": result.expected,
        "status": result.status,
    }
