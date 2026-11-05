from __future__ import annotations

from datetime import date

from omw.sources.google_ads import REPORTS, entities_query, performance_query, shape_entity, shape_performance
from omw.sources.google_ads.rows import ENTITY_COLUMNS, performance_columns
from omw.sources.windows import Window
from tests.unit.google_ads.conftest import CUSTOMER, fixture_results

WINDOW = Window(date(2026, 10, 1), date(2026, 10, 3))


def test_performance_queries_select_the_window_and_skip_removed_campaigns() -> None:
    query = performance_query(REPORTS["campaigns"], WINDOW)
    assert query.startswith("SELECT customer.id, customer.currency_code, campaign.id")
    metrics = "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, "
    metrics += "metrics.conversions_value"
    assert f"segments.date, {metrics}" in query
    assert "FROM campaign WHERE segments.date BETWEEN '2026-10-01' AND '2026-10-03'" in query
    assert "campaign.status != 'REMOVED'" in query
    ad_groups = performance_query(REPORTS["ad_groups"], WINDOW)
    assert "FROM ad_group" in ad_groups and "ad_group.id, ad_group.name, ad_group.status" in ad_groups
    assert "FROM campaign WHERE campaign.status != 'REMOVED'" in entities_query()


def test_campaign_rows_are_flat_and_typed() -> None:
    row = shape_performance(REPORTS["campaigns"], fixture_results("campaigns_stream")[0])
    assert row["source"] == "google_ads" and row["report"] == "campaigns"
    assert row["customer_id"] == CUSTOMER and row["currency"] == "EUR"
    assert row["date"] == date(2026, 10, 1)
    assert row["campaign_id"] == "20010001" and row["channel_type"] == "SEARCH"
    assert row["cost_micros"] == 48_500_000 and isinstance(row["cost_micros"], int)
    assert row["conversions"] == 12.0 and row["conversions_value"] == 980.5
    assert set(row) == set(performance_columns(REPORTS["campaigns"]))


def test_ad_group_rows_carry_the_ad_group() -> None:
    row = shape_performance(REPORTS["ad_groups"], fixture_results("ad_groups_stream")[0])
    assert row["report"] == "ad_groups"
    assert row["ad_group_id"] == "30020001" and row["ad_group_name"] == "Brand - Exact"
    assert set(row) == set(performance_columns(REPORTS["ad_groups"]))


def test_ad_groups_sum_to_their_campaign() -> None:
    campaigns = {
        (r["campaign"]["id"], r["segments"]["date"]): int(r["metrics"]["costMicros"])
        for r in fixture_results("campaigns_stream")
    }
    by_campaign: dict[tuple[str, str], int] = {}
    for r in fixture_results("ad_groups_stream"):
        key = (r["campaign"]["id"], r["segments"]["date"])
        by_campaign[key] = by_campaign.get(key, 0) + int(r["metrics"]["costMicros"])
    for key, cost in by_campaign.items():
        assert cost == campaigns[key], key


def test_missing_fields_default_instead_of_failing() -> None:
    row = shape_performance(
        REPORTS["campaigns"], {"customer": {"id": "1"}, "segments": {"date": "2026-10-01"}}
    )
    assert row["campaign_id"] == "" and row["cost_micros"] == 0 and row["conversions"] == 0.0


def test_entities_are_flat() -> None:
    row = shape_entity(fixture_results("campaign_entities")[2])
    assert row["campaign_id"] == "20010003" and row["campaign_status"] == "PAUSED"
    assert row["budget_micros"] == 100_000_000 and row["time_zone"] == "Europe/Berlin"
    assert set(row) == set(ENTITY_COLUMNS)


def test_search_term_rows_carry_the_term_and_its_status() -> None:
    report = REPORTS["search_terms"]
    query = performance_query(report, Window(date(2026, 10, 1), date(2026, 10, 3)))
    assert query.startswith(
        "SELECT customer.id, customer.currency_code, campaign.id, campaign.name, ad_group.id"
    )
    assert "search_term_view.search_term, search_term_view.status, segments.date" in query
    assert "FROM search_term_view WHERE segments.date BETWEEN '2026-10-01' AND '2026-10-03'" in query
    rows = [shape_performance(report, item) for item in fixture_results("search_terms_stream")]
    assert len(rows) == 6 and rows[0]["report"] == "search_terms"
    assert rows[0]["search_term"] == "acme widgets" and rows[0]["search_term_status"] == "ADDED"
    assert rows[1]["search_term_status"] == "NONE" and rows[0]["ad_group_id"] == rows[1]["ad_group_id"]
    assert rows[0]["cost_micros"] == 6_000_000 and rows[0]["clicks"] == 40
    hints = performance_columns(report)
    assert hints["search_term"] == {"data_type": "text", "nullable": False}


def test_asset_group_rows_carry_the_asset_group() -> None:
    report = REPORTS["asset_groups"]
    query = performance_query(report, Window(date(2026, 10, 1), date(2026, 10, 3)))
    assert "asset_group.id, asset_group.name, asset_group.status, segments.date" in query
    assert "FROM asset_group WHERE segments.date BETWEEN" in query
    rows = [shape_performance(report, item) for item in fixture_results("asset_groups_stream")]
    assert len(rows) == 6 and rows[0]["report"] == "asset_groups" and rows[0]["campaign_id"] == "20010003"
    assert rows[0]["asset_group_id"] == "40030001" and rows[0]["asset_group_status"] == "ENABLED"
    assert performance_columns(report)["asset_group_name"] == {"data_type": "text", "nullable": False}
