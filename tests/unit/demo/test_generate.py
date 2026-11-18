"""The generated fixtures: deterministic, synthetic, and consistent inside each source and across them."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omw.demo.fixtures import SOURCES, Manifest, generate, row_count
from omw.demo.fixtures.web import GA4_FILLS, OTHER, SEARCH_FILLS
from omw.demo.world import CAMPAIGN_KEYS, CAMPAIGNS, MIN_DAYS, Calendar, settled
from omw.metrics.campaign_keys import slug
from omw.sources import ga4, google_ads, linkedin_ads, meta_ads, search_console
from tests.unit.demo.conftest import DAYS, TODAY, payload


def _digest(directory: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(directory.rglob("*.json")):
        h.update(path.relative_to(directory).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def test_the_same_arguments_give_the_same_files(demo_dir: Path, tmp_path: Path) -> None:
    generate(tmp_path / "again", TODAY, DAYS)
    assert _digest(tmp_path / "again") == _digest(demo_dir)
    generate(tmp_path / "other", TODAY, DAYS, seed=7)
    assert _digest(tmp_path / "other") != _digest(demo_dir)


def test_manifest_describes_both_snapshots_of_all_seven_sources(demo_dir: Path, manifest: Manifest) -> None:
    assert manifest.today == TODAY and manifest.days == DAYS
    assert manifest.data["snapshots"] == {
        "initial": {"as_of": "2026-11-17"},
        "restated": {"as_of": "2026-11-18"},
    }
    for snapshot, sources in manifest.data["records"].items():
        assert tuple(sources) == SOURCES
        for source, files in sources.items():
            for name, rows in files.items():
                assert rows == row_count(payload(demo_dir, snapshot, source, name)) > 0, (source, name)


def test_a_history_too_short_to_load_is_refused() -> None:
    with pytest.raises(ValueError, match="at least"):
        Calendar(TODAY, MIN_DAYS - 1)


def test_a_campaign_key_is_the_slug_of_the_name_unless_set_by_hand(manifest: Manifest) -> None:
    for campaign in CAMPAIGNS:
        assert (slug(campaign.name) == campaign.key) is not campaign.manual_key, campaign.name
    by_key = {entry["campaign_key"]: entry["campaigns"] for entry in manifest.data["campaign_keys"]}
    assert {c["source"] for c in by_key["widgets_prospecting"]} == {"meta_ads", "google_ads", "linkedin_ads"}
    assert [c["origin"] for c in by_key["abm_tier_1"]] == ["manual"]


def test_every_restatement_settles_inside_the_window_the_daily_sync_refetches() -> None:
    """Otherwise one daily run would leave the warehouse short of the restated snapshot."""
    assert settled("meta_ads", meta_ads.CADENCES["daily"]) == (1.0, 1.0)
    assert settled("google_ads", google_ads.source.RE_PULL_DAYS) == (1.0, 1.0)
    assert settled("linkedin_ads", linkedin_ads.RE_PULL_DAYS) == (1.0, 1.0)
    assert len(GA4_FILLS) < ga4.RE_PULL_DAYS
    assert len(SEARCH_FILLS) < search_console.windows.DAILY_WINDOW_DAYS


def _sums(rows: list[dict[str, Any]], key: Any, value: Any) -> dict[Any, float]:
    found: dict[Any, float] = defaultdict(float)
    for row in rows:
        found[key(row)] += value(row)
    return found


def test_meta_breakdowns_add_up_to_the_campaign_and_the_ad_level_undercounts(demo_dir: Path) -> None:
    def day(row: dict[str, Any]) -> tuple[str, str]:
        return row["campaign_id"], row["date_start"]

    def cents(row: dict[str, Any]) -> float:
        return round(float(row["spend"]) * 100)

    campaigns = _sums(payload(demo_dir, "restated", "meta_ads", "insights_campaigns")["data"], day, cents)
    for name in ("age_gender", "countries", "placements"):
        assert (
            _sums(payload(demo_dir, "restated", "meta_ads", f"insights_{name}")["data"], day, cents)
            == campaigns
        )
    ads = _sums(payload(demo_dir, "restated", "meta_ads", "insights_ads")["data"], day, cents)
    assert all(ads[key] <= total for key, total in campaigns.items())
    assert sum(ads.values()) < sum(campaigns.values())  # the paused ad set's ad is left out


def _stream(directory: Path, name: str) -> list[dict[str, Any]]:
    return [row for chunk in payload(directory, "restated", "google_ads", name) for row in chunk["results"]]


def test_google_ad_groups_add_up_and_search_terms_cover_part_of_the_campaign(demo_dir: Path) -> None:
    def day(row: dict[str, Any]) -> tuple[str, str]:
        return row["campaign"]["id"], row["segments"]["date"]

    def cost(row: dict[str, Any]) -> float:
        return int(row["metrics"]["costMicros"])

    campaigns = _sums(_stream(demo_dir, "campaigns_stream"), day, cost)
    groups = _sums(_stream(demo_dir, "ad_groups_stream"), day, cost)
    assets = _sums(_stream(demo_dir, "asset_groups_stream"), day, cost)
    assert groups | assets == campaigns  # ad groups, or asset groups for Performance Max, never both
    assert not set(groups) & set(assets)
    terms = _sums(_stream(demo_dir, "search_terms_stream"), day, cost)
    assert terms and all(0 < value < campaigns[key] for key, value in terms.items() if campaigns[key] > 1e6)
    assert {row["customer"]["currencyCode"] for row in _stream(demo_dir, "campaigns_stream")} == {
        "USD",
        "EUR",
    }


def _ga4(directory: Path, grain: str, metric: int = 0) -> tuple[dict[str, float], list[list[str]]]:
    rows = payload(directory, "restated", "ga4", grain)["rows"]
    sums = _sums(
        rows, lambda r: r["dimensionValues"][0]["value"], lambda r: float(r["metricValues"][metric]["value"])
    )
    return sums, [[v["value"] for v in row["dimensionValues"][1:]] for row in rows]


def test_ga4_breakdowns_add_up_to_core_and_keep_an_other_row(demo_dir: Path) -> None:
    core, _ = _ga4(demo_dir, "core")
    for grain in ("channels", "landing_pages", "campaigns"):
        sums, dimensions = _ga4(demo_dir, grain)
        assert sums == core, grain
        if grain != "channels":
            assert any(values[0] == OTHER for values in dimensions), grain
    _, campaigns = _ga4(demo_dir, "campaigns")
    assert set(CAMPAIGN_KEYS) <= {values[0] for values in campaigns}


def test_search_console_queries_are_partly_anonymised(demo_dir: Path) -> None:
    def clicks(grain: str) -> dict[str, float]:
        rows = payload(demo_dir, "restated", "search_console", grain)["rows"]
        return _sums(rows, lambda r: r["keys"][0], lambda r: r["clicks"])

    core = clicks("core")
    assert clicks("devices") == core and clicks("countries") == core
    assert all(value <= core[day] for day, value in clicks("pages").items())
    queries = clicks("queries")
    share = sum(queries.values()) / sum(core.values())
    assert 0.6 < share < 0.75  # the rest belongs to queries Google does not disclose
    assert clicks("query_page") == queries


def test_stripe_and_twenty_carry_the_ad_campaign_keys(demo_dir: Path) -> None:
    customers = payload(demo_dir, "restated", "stripe", "customers")["data"]
    keyed = {c["metadata"].get("campaign_key", "") for c in customers}
    sales = {c.key for c in CAMPAIGNS if c.goal == "sales"}
    assert sales <= keyed <= set(CAMPAIGN_KEYS) | {""}
    assert "" in keyed  # organic customers have no attribution at all
    crm: set[str] = set()
    for name in ("companies", "people"):
        crm |= {r["campaignKey"] for r in payload(demo_dir, "restated", "twenty", name)["data"][name]}
    leads = {c.key for c in CAMPAIGNS if c.goal == "leads"}
    assert leads <= crm <= set(CAMPAIGN_KEYS) | {""}
    opportunities = payload(demo_dir, "restated", "twenty", "opportunities")["data"]["opportunities"]
    assert all(o["campaignKey"] == "" for o in opportunities)  # the marts resolve it from the contact


def test_ga4_key_events_are_the_stripe_sign_ups_of_the_day(demo_dir: Path) -> None:
    events, _ = _ga4(demo_dir, "core", metric=5)
    customers = payload(demo_dir, "restated", "stripe", "customers")["data"]
    created: dict[str, int] = defaultdict(int)
    for customer in customers:
        created[datetime.fromtimestamp(customer["created"], tz=UTC).strftime("%Y%m%d")] += 1
    settled_days = [day for day in events if day <= (TODAY - timedelta(days=4)).strftime("%Y%m%d")]
    assert len(settled_days) > 80
    assert sum(events[day] for day in settled_days) == sum(created[day] for day in settled_days)


def test_lead_forms_match_the_lead_campaigns_conversions(demo_dir: Path) -> None:
    leads = payload(demo_dir, "restated", "meta_ads", "leads")["data"]
    insights = payload(demo_dir, "restated", "meta_ads", "insights_campaigns")["data"]
    cutoff = (TODAY - timedelta(days=8)).isoformat()
    reported = sum(
        int(action["value"])
        for row in insights
        if row["date_start"] <= cutoff
        for action in row.get("actions", [])
        if action["action_type"] == "lead"
    )
    assert reported == sum(1 for lead in leads if lead["created_time"][:10] <= cutoff) > 100


def test_the_restated_snapshot_adds_a_day_and_revises_only_recent_ones(demo_dir: Path) -> None:
    def by_day(snapshot: str) -> dict[str, list[dict[str, Any]]]:
        found: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in payload(demo_dir, snapshot, "meta_ads", "insights_campaigns")["data"]:
            found[row["date_start"]].append(row)
        return found

    before, after = by_day("initial"), by_day("restated")
    assert sorted(set(after) - set(before)) == ["2026-11-17"]
    changed = sorted(day for day in before if before[day] != after[day])
    assert changed and changed[0] >= (TODAY - timedelta(days=meta_ads.CADENCES["daily"])).isoformat()


def test_stripe_invoices_change_state_between_the_snapshots(demo_dir: Path) -> None:
    def status(snapshot: str) -> dict[str, str]:
        return {i["id"]: i["status"] for i in payload(demo_dir, snapshot, "stripe", "invoices")["data"]}

    before, after = status("initial"), status("restated")
    moved = {(before[i], after[i]) for i in before if before[i] != after[i]}
    assert ("open", "paid") in moved
    assert set(before) < set(after)
    assert {"paid", "open", "uncollectible"} <= set(after.values())


def test_twenty_opportunities_move_stage_and_the_timeline_records_it(demo_dir: Path) -> None:
    data = payload(demo_dir, "restated", "twenty", "opportunities")["data"]["opportunities"]
    stages = {o["stage"] for o in data}
    assert {"NEW", "MEETING", "CUSTOMER", "LOST"} <= stages
    assert any(o["deletedAt"] for o in data)
    feed = payload(demo_dir, "restated", "twenty", "timeline_activities")["data"]["timelineActivities"]
    won = [e for e in feed if e["properties"].get("diff", {}).get("stage", {}).get("after") == "CUSTOMER"]
    assert len(won) == sum(1 for o in data if o["stage"] == "CUSTOMER")


def test_identifiers_are_synthetic(demo_dir: Path) -> None:
    for name in ("customers",):
        for customer in payload(demo_dir, "restated", "stripe", name)["data"]:
            assert customer["email"].endswith(".example") and customer["id"].startswith("cus_DEMO")
    for person in payload(demo_dir, "restated", "twenty", "people")["data"]["people"]:
        assert person["emails"]["primaryEmail"].endswith(".example")
    for lead in payload(demo_dir, "restated", "meta_ads", "leads")["data"]:
        assert lead["field_data"][0]["values"][0].endswith("@example.test")
