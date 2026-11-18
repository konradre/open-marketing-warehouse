"""Fixture mode: the real clients and dlt sources read the generated files through the replay transport.
No request leaves the process and none goes unanswered silently."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from omw.demo.fixtures import Manifest
from omw.demo.replay import TOKEN, Replay, token_provider
from omw.demo.world import (
    GA4_PROPERTY,
    GOOGLE_EUR,
    GOOGLE_USD,
    LINKEDIN_ACCOUNT,
    META_ACCOUNT,
    SITE_URL,
    TWENTY_URL,
)
from omw.pacing import MetaPacer
from omw.sources.ga4 import GA4AccessError, GA4Client
from omw.sources.ga4.grains import METRICS
from omw.sources.google_ads.client import GoogleAdsClient, GoogleAdsQueryError
from omw.sources.google_ads.queries import REPORTS, entities_query, performance_query
from omw.sources.linkedin_ads import LINKEDIN_VERSION, LinkedInAuth, linkedin_ads
from omw.sources.meta_ads import meta_ads
from omw.sources.search_console import SearchConsoleAccessError, SearchConsoleClient
from omw.sources.stripe import StripeClient
from omw.sources.twenty import twenty
from omw.sources.windows import Window
from tests.unit.demo.conftest import TODAY, payload

WEEK = Window(TODAY - timedelta(days=7), TODAY - timedelta(days=1))


def _in_week(day: str) -> bool:
    return WEEK.start.isoformat() <= day <= WEEK.end.isoformat()


def test_search_console_pages_and_honours_the_window(replay: Replay, demo_dir: Path) -> None:
    client = SearchConsoleClient(token_provider, session=replay.session, row_limit=100)
    rows = list(client.query(SITE_URL, WEEK.start, WEEK.end, ["date", "query", "page"]))
    expected = [
        r
        for r in payload(demo_dir, "restated", "search_console", "query_page")["rows"]
        if _in_week(r["keys"][0])
    ]
    assert rows == expected and len(rows) > 200
    assert [call.body["startRow"] for call in replay.adapter.calls] == [0, 100, 200]
    assert (
        list(client.query(SITE_URL, date(2025, 1, 1), date(2025, 3, 31), ["date"])) == []
    )  # before the company
    assert list(client.query(SITE_URL, WEEK.start, WEEK.end, ["date"], search_type="image")) == []


def test_a_property_the_fixtures_do_not_hold_is_refused_like_the_platform_would(replay: Replay) -> None:
    with pytest.raises(SearchConsoleAccessError):
        list(
            SearchConsoleClient(token_provider, session=replay.session).query(
                "sc-domain:other.example", WEEK.start, WEEK.end, ["date"]
            )
        )
    with pytest.raises(GA4AccessError):
        list(
            GA4Client(token_provider, session=replay.session).run_report(
                "999", WEEK.start, WEEK.end, ["date"], METRICS
            )
        )
    with pytest.raises(GoogleAdsQueryError):
        GoogleAdsClient(token_provider, "dev", session=replay.session).search_stream(
            "9999999999", entities_query()
        )


def test_ga4_pages_with_offset(replay: Replay, demo_dir: Path) -> None:
    client = GA4Client(token_provider, session=replay.session, limit=50)
    dimensions = ["date", "sessionCampaignName", "sessionSource", "sessionMedium"]
    rows = list(client.run_report(GA4_PROPERTY, WEEK.start, WEEK.end, dimensions, METRICS))
    expected = [
        r
        for r in payload(demo_dir, "restated", "ga4", "campaigns")["rows"]
        if _in_week(
            f"{r['dimensionValues'][0]['value'][:4]}-{r['dimensionValues'][0]['value'][4:6]}-{r['dimensionValues'][0]['value'][6:]}"
        )
    ]
    assert rows == expected and len(rows) > 100
    assert [call.body["offset"] for call in replay.adapter.calls] == [0, 50, 100]


def test_google_ads_answers_the_gaql_it_is_sent(replay: Replay) -> None:
    client = GoogleAdsClient(token_provider, "fixture-developer-token", session=replay.session)
    entities = client.search_stream(GOOGLE_USD, entities_query())
    assert {row["campaign"]["name"] for row in entities} >= {"Brand - Search", "Gadgets Launch"}
    assert [
        row["customer"]["currencyCode"] for row in client.search_stream(GOOGLE_EUR, entities_query())
    ] == ["EUR"]
    terms = client.search_stream(GOOGLE_USD, performance_query(REPORTS["search_terms"], WEEK))
    assert terms and all(_in_week(row["segments"]["date"]) for row in terms)
    assert {row["customer"]["id"] for row in terms} == {GOOGLE_USD}
    assert client.search_stream(GOOGLE_EUR, performance_query(REPORTS["asset_groups"], WEEK)) == []


def test_stripe_lists_newest_first_in_pages(replay: Replay, demo_dir: Path) -> None:
    client = StripeClient("fixture-key", session=replay.session, page_limit=100)
    invoices = list(client.list("invoices"))
    assert invoices == payload(demo_dir, "restated", "stripe", "invoices")["data"]
    assert len(replay.adapter.calls) == len(invoices) // 100 + 1
    assert replay.adapter.calls[1].query["starting_after"] == invoices[99]["id"]
    cutoff = invoices[40]["created"]
    recent = list(client.list("invoices", {"created[gte]": cutoff}))
    assert recent and all(invoice["created"] >= cutoff for invoice in recent) and len(recent) < len(invoices)


def _rows(resource: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in resource:
        found += item if isinstance(item, list) else [item]
    return found


def test_twenty_source_reads_live_and_deleted_records_through_the_cursor(
    replay: Replay, demo_dir: Path
) -> None:
    source = twenty(base_url=TWENTY_URL, api_key="fixture-key", session=replay.session)
    records = payload(demo_dir, "restated", "twenty", "opportunities")["data"]["opportunities"]
    live = _rows(source.resources["opportunities"])
    assert {row["id"] for row in live} == {r["id"] for r in records if r["deletedAt"] is None}
    gone = _rows(source.resources["deleted_opportunities"])
    assert {row["id"] for row in gone} == {r["id"] for r in records if r["deletedAt"]} != set()
    companies = _rows(source.resources["companies"])
    assert len(companies) == 222
    pages = [
        c
        for c in replay.adapter.calls
        if c.path == "/rest/companies" and "deletedAt" not in c.query["filter"]
    ]
    assert len(pages) == 4 and all("starting_after" in call.query for call in pages[1:])  # 60 a page


def test_linkedin_source_reads_entities_and_windowed_analytics(replay: Replay, demo_dir: Path) -> None:
    replay.session.auth = LinkedInAuth(LINKEDIN_VERSION, token_provider=token_provider)
    source = linkedin_ads(
        account_ids=[LINKEDIN_ACCOUNT],
        auth=replay.session.auth,
        session=replay.session,
        window=WEEK,
        leads=True,
    )
    assert {row["name"] for row in _rows(source.resources["campaigns"])} >= {
        "LI | ABM Tier 1",
        "Gadgets Launch",
    }
    analytics = _rows(source.resources["ad_analytics"])
    assert len(analytics) == 4 * WEEK.days and {row["date"] for row in analytics} == set(WEEK)
    assert {row["currency"] for row in analytics} == {"USD"}
    creatives = _rows(source.resources["ad_analytics_creatives"])
    assert sum(row["cost"] for row in creatives) == sum(row["cost"] for row in analytics)
    leads = _rows(source.resources["lead_form_responses"])
    assert leads and all(row["date"] in WEEK for row in leads)


def test_meta_source_reads_through_the_sdk_and_the_pacer(replay: Replay, demo_dir: Path) -> None:
    pacer = MetaPacer(sleep=lambda _: None)
    source = meta_ads(
        account_ids=[META_ACCOUNT],
        access_token=TOKEN,
        session=replay.new_session(),
        pacer=pacer,
        window=WEEK,
        levels=("campaign", "ad"),
        breakdowns=("countries",),
        leads=True,
        page_size=3,
        sleep=lambda _: None,
    )
    campaigns = _rows(source.resources["insights_campaigns"])
    fixture = payload(demo_dir, "restated", "meta_ads", "insights_campaigns")["data"]
    expected = [r for r in fixture if _in_week(r["date_start"])]
    assert len(campaigns) == len(expected) == 4 * WEEK.days
    assert sum(row["spend"] for row in campaigns) == sum(Decimal(r["spend"]) for r in expected)
    countries = _rows(source.resources["insights_countries"])
    assert sum(row["spend"] for row in countries) == sum(row["spend"] for row in campaigns)
    assert {row["currency"] for row in campaigns} == {"USD"}
    assert len(_rows(source.resources["insights_ads"])) == 2 * len(campaigns)  # two live ads a campaign
    entities = _rows(source.resources["campaigns"])
    assert {row["name"] for row in entities} >= {"Widgets - Prospecting", "Lead Gen - Ebook"}
    assert len(_rows(source.resources["leads"])) == len(
        payload(demo_dir, "restated", "meta_ads", "leads")["data"]
    )

    calls = [call for call in replay.adapter.calls if call.host == "graph.facebook.com"]
    started = [call for call in calls if call.method == "POST" and call.path.endswith("/insights")]
    assert len(started) == 3 * WEEK.days  # one async report run per day and report
    assert all(call.query["access_token"] == TOKEN for call in calls if call.method == "GET")
    pages = [call for call in calls if call.path.endswith(f"/act_{META_ACCOUNT}/campaigns")]
    assert [call.query.get("after") for call in pages] == [None, "3"]  # four campaigns, three a page
    # Every response went through the pacer, which read the usage headers and never had to slow down.
    assert pacer.summary.requests == len(calls) > 100
    assert 0 < pacer.summary.peak_utilisation < pacer.config.slow_at
    assert (pacer.summary.sleeps, pacer.summary.tier, pacer.stopped) == (0, "standard_access", None)


def test_meta_answers_an_unknown_node_with_a_graph_error(replay: Replay) -> None:
    response = replay.session.get("https://graph.facebook.com/v26.0/act_999/campaigns")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "GraphMethodException"


def test_an_unknown_request_gets_a_404_and_never_reaches_the_network(replay: Replay) -> None:
    for url in (
        "https://graph.facebook.com/v22.0/me",
        "http://unknown.example/latest",
        "https://api.stripe.com/v1/charges",
    ):
        response = replay.session.get(url)
        assert response.status_code == 404
        assert "fixture mode has no answer" in response.json()["error"]["message"]
    assert len(replay.adapter.calls) == 3


def test_a_missing_snapshot_says_how_to_create_it(tmp_path: Path, manifest: Manifest) -> None:
    with pytest.raises(FileNotFoundError, match="omw demo generate"):
        Replay(tmp_path / "restated", manifest.data["accounts"])
    with pytest.raises(FileNotFoundError, match="omw demo generate"):
        Manifest.read(tmp_path)
