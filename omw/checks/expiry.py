"""Expiry: a credential or a pinned API version that is about to stop working warns ahead of the day and
fails after it, so a source never goes stale by surprise (DESIGN.md §5 for Meta's token, §3 for LinkedIn's
version).

The pipelines cannot ask most platforms when a credential ends, so the date is configuration: one
``token_expires_at`` (ISO date) per source, written down when the credential is created. A source without
one has no row. LinkedIn's version sunset needs no configuration: it follows from the pinned version.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

import dlt

from omw.checks.results import CheckResult, Status
from omw.sources.linkedin_ads.version import LINKEDIN_VERSION, WARN_MONTHS, check_version

CHECK = "expiry"
DEFAULT_WARN_DAYS = 14
VERSION_WARN_DAYS = WARN_MONTHS * 31

CREDENTIALS: dict[str, str] = {
    "search_console": "service-account key",
    "meta_ads": "system-user token",
    "google_ads": "OAuth refresh token",
    "ga4": "service-account key",
    "stripe": "restricted API key",
    "linkedin_ads": "OAuth refresh token",
    "twenty": "API key",
}
"""source -> the credential its ``token_expires_at`` is about, in the words of the alert."""


def expiry_key(source: str) -> str:
    return f"sources.{source}.token_expires_at"


def read_expiries(sources: Iterable[str]) -> dict[str, Any]:
    """source -> its configured ``token_expires_at`` (or ``None``), for the sources given."""
    return {source: dlt.config.get(expiry_key(source), str) for source in sorted(sources)}


def read_warn_days() -> int:
    return int(dlt.config.get("checks.expiry_warn_days", int) or DEFAULT_WARN_DAYS)


def _token_result(source: str, expires_at: Any, today: date, warn_days: int) -> CheckResult | None:
    if expires_at is None or str(expires_at).strip() == "":
        return None
    what = CREDENTIALS.get(source, "credential")
    key = expiry_key(source)
    try:
        day = expires_at if isinstance(expires_at, date) else date.fromisoformat(str(expires_at).strip())
    except ValueError:
        return CheckResult(
            source,
            CHECK,
            "token",
            Status.FAIL,
            0.0,
            float(warn_days),
            f"cannot read {key} = {expires_at!r} as an ISO date (YYYY-MM-DD)",
        )
    days_left = (day - today).days
    if days_left < 0:
        status = Status.FAIL
        detail = f"the {what} expired on {day} ({-days_left} days ago): replace it, then update {key}"
    elif days_left <= warn_days:
        status = Status.WARN
        detail = f"the {what} expires on {day} (in {days_left} days): replace it, then update {key}"
    else:
        status, detail = Status.PASS, f"the {what} expires on {day} (in {days_left} days)"
    return CheckResult(source, CHECK, "token", status, float(days_left), float(warn_days), detail)


def _linkedin_version_result(today: date, version: str) -> CheckResult:
    found = check_version(version, today)
    days_left = (found.sunset - today).days
    status = Status.FAIL if found.expired else Status.WARN if found.expiring else Status.PASS
    return CheckResult(
        "linkedin_ads",
        CHECK,
        "api_version",
        status,
        float(days_left),
        float(VERSION_WARN_DAYS),
        found.message,
    )


def expiry_checks(
    expiries: Mapping[str, Any],
    today: date,
    warn_days: int = DEFAULT_WARN_DAYS,
    linkedin_version: str = LINKEDIN_VERSION,
) -> list[CheckResult]:
    """One ``token`` row per source in ``expiries`` that has a date, and LinkedIn's ``api_version`` row when
    LinkedIn is among them."""
    results: list[CheckResult] = []
    for source, expires_at in expiries.items():
        result = _token_result(source, expires_at, today, warn_days)
        if result is not None:
            results.append(result)
        if source == "linkedin_ads":
            results.append(_linkedin_version_result(today, linkedin_version))
    return results
