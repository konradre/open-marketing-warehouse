"""Version pin, OAuth refresh, resources through the mocked API, shapes, and the pipeline on dummy."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import dlt
import pytest
import requests
import responses

from omw.pipelines.linkedin_ads import run_daily
from omw.pipelines.marts import SQL_DIR, statements
from omw.sources.linkedin_ads import (
    LINKEDIN_VERSION,
    LinkedInAuth,
    check_version,
    linkedin_ads,
    refresh_access_token,
)
from omw.sources.linkedin_ads.auth import TOKEN_URL
from omw.sources.linkedin_ads.leads import lead_responses_path
from omw.sources.linkedin_ads.shapes import shape_analytics, shape_campaign, shape_campaign_group, urn_id
from omw.sources.linkedin_ads.source import API_BASE, analytics_path
from omw.sources.linkedin_ads.version import VersionExpiredError
from omw.sources.windows import Window
from tests.unit.linkedin_ads.conftest import ACCOUNT, VERSION, body, fast_session, fixture, register_api

WINDOW = Window(date(2026, 10, 1), date(2026, 10, 3))
TODAY = date(2026, 10, 5)


def test_version_is_pinned_once_and_checked_against_the_support_window() -> None:
    assert LINKEDIN_VERSION == VERSION and len(LINKEDIN_VERSION) == 6
    fresh = check_version("202610", date(2026, 11, 5))
    assert (
        not fresh.expired
        and not fresh.expiring
        and fresh.sunset == date(2027, 10, 1)
        and fresh.months_left == 10
    )
    warning = check_version("202610", date(2027, 7, 15))
    assert (
        warning.expiring
        and not warning.expired
        and warning.months_left <= 3
        and "plan the bump" in warning.message
    )
    gone = check_version("202610", date(2027, 10, 1))
    assert gone.expired and "bump LINKEDIN_VERSION" in gone.message
    with pytest.raises(ValueError):
        check_version("2026-10")


@responses.activate
def test_refresh_token_flow_and_headers() -> None:
    responses.post(TOKEN_URL, body=body("token"), content_type="application/json")
    token, expires_at = refresh_access_token("cid", "secret", "AQW-refresh")
    assert token == "AQV-fresh-access-token" and expires_at > 0
    sent = responses.calls[0].request.body
    assert "grant_type=refresh_token" in str(sent) and "refresh_token=AQW-refresh" in str(sent)
    auth = LinkedInAuth(VERSION, client_id="cid", client_secret="secret", refresh_token="AQW-refresh")
    request = auth(requests.Request("GET", f"{API_BASE}adAccounts/1").prepare())
    assert request.headers["Authorization"] == "Bearer AQV-fresh-access-token"
    assert (
        request.headers["LinkedIn-Version"] == VERSION
        and request.headers["X-Restli-Protocol-Version"] == "2.0.0"
    )
    assert (
        auth.token() == "AQV-fresh-access-token" and len(responses.calls) == 2
    )  # one direct, one by auth; then cached


@responses.activate
def test_invalid_refresh_token_is_an_access_error() -> None:
    responses.post(TOKEN_URL, body=body("token_invalid"), status=400, content_type="application/json")
    with pytest.raises(PermissionError, match="Advertising API"):
        refresh_access_token("cid", "secret", "bad")


def test_shapes_flatten_urns_money_and_stamps() -> None:
    assert (
        urn_id("urn:li:sponsoredCampaign:800001") == "800001" and urn_id(123) == "123" and urn_id(None) == ""
    )
    group = shape_campaign_group(fixture("campaign_groups")["elements"][0])
    assert (
        group["account_id"] == ACCOUNT
        and group["daily_budget"] == Decimal("400")
        and group["total_budget"] == Decimal("12000")
    )
    assert group["budget_optimization"] is True and group["currency"] == "EUR"
    assert group["run_start"] == datetime.fromtimestamp(1788566400, tz=UTC)
    plain = shape_campaign_group(fixture("campaign_groups")["elements"][1])
    assert (
        plain["daily_budget"] == Decimal(0)
        and plain["budget_optimization"] is False
        and plain["run_end"].year == 1970
    )
    campaign = shape_campaign(fixture("campaigns_page_1")["elements"][0])
    assert (
        campaign["campaign_group_id"] == "700001"
        and campaign["unit_cost"] == Decimal("12.5")
        and campaign["cost_type"] == "CPM"
    )
    row = shape_analytics(ACCOUNT, "EUR", fixture("ad_analytics")["elements"][0])
    assert (
        row["date"] == date(2026, 10, 1)
        and row["campaign_id"] == "800001"
        and row["cost"] == Decimal("210.40")
    )
    assert row["report"] == "campaigns" and row["conversions"] == 6 and row["currency"] == "EUR"


def test_analytics_path_keeps_restli_syntax_literal() -> None:
    path = analytics_path(ACCOUNT, WINDOW)
    assert path.startswith("adAnalytics?q=analytics&pivot=CAMPAIGN&timeGranularity=DAILY")
    assert f"&accounts=List(urn%3Ali%3AsponsoredAccount%3A{ACCOUNT})" in path
    assert "&dateRange=(start:(year:2026,month:10,day:1),end:(year:2026,month:10,day:3))" in path
    assert "&fields=dateRange,pivotValues,impressions,clicks,costInLocalCurrency" in path


@responses.activate
def test_resources_page_with_the_cursor_and_carry_the_headers(auth: LinkedInAuth) -> None:
    register_api(responses)
    session = fast_session(auth)
    source = linkedin_ads(account_ids=[ACCOUNT], auth=auth, session=session, window=WINDOW)
    assert set(source.resources) == {
        "ad_accounts",
        "campaign_groups",
        "campaigns",
        "creatives",
        "ad_analytics",
        "ad_analytics_creatives",
        "sync_windows",
    }
    campaigns = list(source.resources["campaigns"])
    assert [c["id"] for c in campaigns] == ["800001", "800002", "800003"]
    assert all(c["account_id"] == ACCOUNT for c in campaigns)
    groups = list(source.resources["campaign_groups"])
    assert [g["id"] for g in groups] == ["700001", "700002"]
    analytics = list(source.resources["ad_analytics"])
    assert len(analytics) == 6 and {r["date"] for r in analytics} == {
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 3),
    }
    assert all(r["currency"] == "EUR" and r["account_id"] == ACCOUNT for r in analytics)
    api_calls = [c.request for c in responses.calls if "api.linkedin.com" in str(c.request.url)]
    assert all(
        r.headers["LinkedIn-Version"] == VERSION and r.headers["Authorization"] == "Bearer test-token"
        for r in api_calls
    )
    hints: dict[str, Any] = dict(source.resources["ad_analytics"].compute_table_schema())
    assert hints["x-merge-strategy"] == "delete-insert"
    assert [c for c, h in hints["columns"].items() if h.get("merge_key")] == ["account_id", "date"]
    assert hints["x-clickhouse-sort"] == ["account_id", "date", "campaign_id"]
    entity: dict[str, Any] = dict(source.resources["campaigns"].compute_table_schema())
    assert entity["write_disposition"] == "merge" and [
        c for c, h in entity["columns"].items() if h.get("primary_key")
    ] == ["id"]


@responses.activate
def test_rate_limit_is_retried_up_to_the_bound_then_fails(auth: LinkedInAuth) -> None:
    session = fast_session(auth)
    for _ in range(3):
        responses.get(
            f"{API_BASE}adAccounts/{ACCOUNT}",
            body=body("rate_limited"),
            status=429,
            content_type="application/json",
        )
    source = linkedin_ads(account_ids=[ACCOUNT], auth=auth, session=session, window=WINDOW)
    with pytest.raises(Exception, match="429"):
        list(source.resources["ad_analytics"])
    assert len(responses.calls) == 3  # retried up to the bound, then given up


@responses.activate
def test_a_retired_version_fails_without_a_retry(auth: LinkedInAuth) -> None:
    session = fast_session(auth)
    responses.get(
        f"{API_BASE}adAccounts/{ACCOUNT}",
        body=body("version_retired"),
        status=426,
        content_type="application/json",
    )
    source = linkedin_ads(account_ids=[ACCOUNT], auth=auth, session=session, window=WINDOW)
    with pytest.raises(Exception, match="426"):
        list(source.resources["ad_analytics"])
    assert len(responses.calls) == 1


def test_validation(auth: LinkedInAuth) -> None:
    with pytest.raises(ValueError, match="account id"):
        linkedin_ads(account_ids=[], auth=auth)
    with pytest.raises(ValueError, match="client_id"):
        linkedin_ads(account_ids=[ACCOUNT], client_id=None, client_secret=None, refresh_token=None)


def _pipeline(tmp_path: Path) -> dlt.Pipeline:
    return dlt.pipeline(
        pipeline_name="linkedin_unit",
        destination=dlt.destinations.dummy(completed_prob=1.0),
        dataset_name="linkedin_ads",
        pipelines_dir=str(tmp_path),
    )


@responses.activate
def test_daily_run_checks_the_version_then_loads(tmp_path: Path, auth: LinkedInAuth) -> None:
    register_api(responses)
    session = fast_session(auth)
    counts = run_daily(
        _pipeline(tmp_path), today=TODAY, account_ids=[ACCOUNT], auth=auth, session=session, marts_sql=None
    )
    assert counts["ad_analytics"] == 6 and counts["campaigns"] == 3 and counts["campaign_groups"] == 2
    assert counts["creatives"] == 2 and counts["ad_accounts"] == 1 and counts["sync_windows"] == 1
    assert counts["ad_analytics_creatives"] == 6
    analytics_call = next(c.request for c in responses.calls if "adAnalytics" in str(c.request.url))
    assert "dateRange=(start:(year:2026,month:9,day:28),end:(year:2026,month:10,day:4))" in str(
        analytics_call.url
    )


def test_daily_run_refuses_a_retired_version(tmp_path: Path, auth: LinkedInAuth) -> None:
    with pytest.raises(VersionExpiredError, match="bump LINKEDIN_VERSION"):
        run_daily(
            _pipeline(tmp_path), today=date(2028, 1, 1), account_ids=[ACCOUNT], auth=auth, marts_sql=None
        )


def test_marts_sql_views() -> None:
    from omw.pipelines.marts import SQL_DIR, statements

    stmts = statements((SQL_DIR / "80_linkedin_ads_marts.sql").read_text())
    assert len(stmts) == 3 and all(
        "DEFINER = CURRENT_USER SQL SECURITY DEFINER AS" in s and ";" not in s for s in stmts
    )
    assert "FROM raw.linkedin_ads___ad_analytics AS a" in stmts[0] and "group_budget_optimization" in stmts[1]
    assert "FROM raw.linkedin_ads___ad_analytics_creatives" in stmts[2] and "creative_id" in stmts[2]


@responses.activate
def test_creative_pivot_lands_in_its_own_table_keyed_by_creative(auth: LinkedInAuth) -> None:
    register_api(responses)
    path = analytics_path(ACCOUNT, WINDOW, pivot="CREATIVE")
    assert path.startswith("adAnalytics?q=analytics&pivot=CREATIVE&timeGranularity=DAILY")
    source = linkedin_ads(account_ids=[ACCOUNT], auth=auth, session=fast_session(auth), window=WINDOW)
    rows = list(source.resources["ad_analytics_creatives"])
    assert len(rows) == 6 and rows[0]["report"] == "creatives" and rows[0]["creative_id"] == "900001"
    assert "campaign_id" not in rows[0] and rows[0]["cost"] == Decimal("189.36")
    hints: dict[str, Any] = dict(source.resources["ad_analytics_creatives"].compute_table_schema())
    assert hints["x-clickhouse-sort"] == ["account_id", "date", "creative_id"]
    assert "creative_id" in hints["columns"] and "campaign_id" not in hints["columns"]


@responses.activate
def test_lead_form_responses_are_optional_counted_and_keep_answers_in_raw(auth: LinkedInAuth) -> None:
    register_api(responses)
    path = lead_responses_path(ACCOUNT, WINDOW)
    assert path.startswith("leadFormResponses?q=owner&owner=(sponsoredAccount:urn%3Ali%3AsponsoredAccount%3A")
    assert (
        "&leadType=(leadType:SPONSORED)&submittedAtTimeRange=(start:1790812800000,end:1791071999999)" in path
    )
    plain = linkedin_ads(account_ids=[ACCOUNT], auth=auth, session=fast_session(auth), window=WINDOW)
    assert "lead_form_responses" not in plain.resources
    source = linkedin_ads(
        account_ids=[ACCOUNT], auth=auth, session=fast_session(auth), window=WINDOW, leads=True
    )
    rows = list(source.resources["lead_form_responses"])
    assert [r["id"] for r in rows] == [f"urn:li:leadFormResponse:{3000000 + i}" for i in (1, 2, 3)]
    assert rows[0]["campaign_id"] == "800001" and rows[2]["creative_id"] == "900002"
    assert rows[0]["form_id"] == "6100001" and rows[0]["date"] == date(2026, 10, 1)
    assert rows[0]["answer_count"] == 2 and "example.test" in rows[0]["answers"]
    hints: dict[str, Any] = dict(source.resources["lead_form_responses"].compute_table_schema())
    assert hints["write_disposition"] == "merge" and hints["x-clickhouse-sort"] == ["id"]
    stmts = statements((SQL_DIR / "80_linkedin_ads_marts_leads.sql").read_text())
    assert len(stmts) == 1 and "l.answers" not in stmts[0]
    assert "FROM raw.linkedin_ads___lead_form_responses" in stmts[0]
