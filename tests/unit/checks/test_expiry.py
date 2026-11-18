"""Expiry: a credential with a recorded end date warns ahead of it and fails after it."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from omw.checks import expiry
from omw.checks.expiry import expiry_checks, read_expiries
from omw.checks.results import Status
from omw.checks.runner import run_checks
from tests.unit.checks.conftest import FakeWarehouse

TODAY = date(2026, 11, 18)


def one(source: str, expires_at: object, warn_days: int = 14) -> tuple[Status, float, str]:
    (result,) = expiry_checks({source: expires_at}, TODAY, warn_days)
    assert (result.source, result.check, result.report, result.expected) == (
        source,
        "expiry",
        "token",
        warn_days,
    )
    return result.status, result.observed, result.detail


def test_far_away_passes_close_warns_past_fails() -> None:
    assert one("meta_ads", "2027-01-01") == (
        Status.PASS,
        44.0,
        "the system-user token expires on 2027-01-01 (in 44 days)",
    )
    status, days, detail = one("meta_ads", "2026-12-02")
    assert (status, days) == (Status.WARN, 14.0)  # the warning starts on the first day inside the window
    assert detail == (
        "the system-user token expires on 2026-12-02 (in 14 days): replace it, then update "
        "sources.meta_ads.token_expires_at"
    )
    assert one("meta_ads", "2026-12-03")[0] is Status.PASS
    assert one("twenty", date(2026, 11, 18))[:2] == (Status.WARN, 0.0)  # still valid on its last day
    status, days, detail = one("stripe", "2026-11-17")
    assert (status, days) == (Status.FAIL, -1.0)
    assert detail.startswith("the restricted API key expired on 2026-11-17 (1 days ago): replace it")
    assert one("ga4", "2026-12-10", warn_days=30)[0] is Status.WARN


def test_a_source_without_a_date_has_no_row_and_a_bad_date_fails_loudly() -> None:
    assert expiry_checks({"ga4": None, "stripe": "", "google_ads": "  "}, TODAY) == []
    status, _, detail = one("google_ads", "next spring")
    assert status is Status.FAIL
    assert (
        detail
        == "cannot read sources.google_ads.token_expires_at = 'next spring' as an ISO date (YYYY-MM-DD)"
    )


def test_linkedin_also_reports_its_pinned_api_version() -> None:
    token, version = expiry_checks({"linkedin_ads": "2027-06-01"}, TODAY, linkedin_version="202610")
    assert (token.report, token.status) == ("token", Status.PASS)
    assert (version.report, version.status, version.observed) == ("api_version", Status.PASS, 317.0)
    assert version.detail == "LinkedIn-Version 202610 is supported until 2027-10-01"
    (soon,) = expiry_checks({"linkedin_ads": None}, date(2027, 8, 1), linkedin_version="202610")
    assert soon.status is Status.WARN and "plan the bump" in soon.detail
    (gone,) = expiry_checks({"linkedin_ads": None}, date(2027, 10, 1), linkedin_version="202610")
    assert gone.status is Status.FAIL and gone.observed == 0.0


def test_dates_come_from_each_sources_own_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCES__STRIPE__TOKEN_EXPIRES_AT", "2027-03-01")
    monkeypatch.delenv("SOURCES__GA4__TOKEN_EXPIRES_AT", raising=False)
    assert read_expiries(["stripe", "ga4"]) == {"ga4": None, "stripe": "2027-03-01"}
    assert set(expiry.CREDENTIALS) == {
        "search_console",
        "meta_ads",
        "google_ads",
        "ga4",
        "stripe",
        "linkedin_ads",
        "twenty",
    }
    monkeypatch.setenv("CHECKS__EXPIRY_WARN_DAYS", "30")
    assert expiry.read_warn_days() == 30


def test_run_checks_adds_expiry_rows_judged_against_the_real_day(warehouse: FakeWarehouse) -> None:
    warehouse.objects = {"raw.ga4___sync_windows"}
    warehouse.when("raw.ga4___sync_windows", [[datetime(2026, 11, 18, 3, 0), 1]])
    now = datetime(2026, 11, 18, 4, 0)
    run = run_checks(warehouse, now, {"ga4": 30.0}, expiries={"ga4": "2026-11-20"}, today=date(2026, 10, 4))
    by_check = {result.check: result for result in run.results}
    assert set(by_check) == {"freshness", "expiry"}
    assert by_check["expiry"].status is Status.WARN and by_check["expiry"].observed == 2.0
    assert run.ok and len(run.warned) == 1
    written = warehouse.inserted[0][1]
    assert ["ga4", "expiry", "token", "warn"] in [row[1:5] for row in written]
    quiet = run_checks(warehouse, now, {"ga4": 30.0}, write=False)
    assert {result.check for result in quiet.results} == {"freshness"}  # opt-in: no dates, no rows
