"""The synthetic world: the same for a seed every time, breakdowns that sum to their parents, grains that
undercount where the real ones do, and raw rows shaped like the tables dlt creates."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pytest

from omw.eval.generate import build_world
from omw.eval.generate.rng import Rng, split
from omw.eval.raw_schema import RAW_TABLES, columns, source_of
from omw.eval.seed import SOURCES
from omw.eval.world import END, SEP, START, Period, World


def _sums(world: World, table: str, keys: tuple[str, ...], column: str) -> dict[tuple[Any, ...], float]:
    found: dict[tuple[Any, ...], float] = defaultdict(float)
    for row in world.tables[table]:
        found[tuple(row[key] for key in keys)] += float(row[column])
    return found


def test_the_same_seed_builds_the_same_world_and_another_seed_does_not(world: World) -> None:
    again = build_world()
    assert again.tables == world.tables and again.raw == world.raw
    other = build_world(seed=world.seed + 1)
    assert other.tables["meta_ads_campaigns"] != world.tables["meta_ads_campaigns"]


def test_draws_depend_on_their_keys_not_on_call_order() -> None:
    rng = Rng(7)
    first = rng.unit("campaign", "2026-09-01", "clicks")
    rng.unit("something", "else")
    assert rng.unit("campaign", "2026-09-01", "clicks") == first
    assert 0 <= first < 1 and rng.integer(3, 5, "x") in (3, 4, 5)
    assert Rng(8).unit("campaign", "2026-09-01", "clicks") != first


@pytest.mark.parametrize(
    ("total", "weights"), [(100, [1, 1, 1]), (7, [0.5, 0.3, 0.2]), (0, [2, 1]), (1, [1, 9])]
)
def test_split_shares_out_whole_numbers_that_add_up(total: int, weights: list[float]) -> None:
    parts = split(total, weights)
    assert sum(parts) == total and all(part >= 0 for part in parts) and len(parts) == len(weights)


def test_split_rejects_what_it_cannot_share_out() -> None:
    with pytest.raises(ValueError, match="negative"):
        split(-1, [1.0])
    with pytest.raises(ValueError, match="positive"):
        split(5, [])


def test_the_world_covers_every_source_and_the_whole_window(world: World) -> None:
    assert {source_of(table) for table in world.raw} == set(SOURCES)
    dates = {row["date"] for row in world.tables["meta_ads_campaigns"]}
    assert min(dates) == START and max(dates) == END
    assert len(world.tables) >= 30 and sum(len(rows) for rows in world.raw.values()) > 30_000


@pytest.mark.parametrize(
    ("parent", "child", "keys", "column"),
    [
        ("meta_ads_campaigns", "meta_ads_age_gender", ("campaign_id", "date"), "spend"),
        ("meta_ads_campaigns", "meta_ads_countries", ("campaign_id", "date"), "clicks"),
        ("meta_ads_campaigns", "meta_ads_placements", ("campaign_id", "date"), "impressions"),
        ("linkedin_ads_campaigns", "linkedin_ads_creatives", ("campaign_id", "date"), "cost"),
        ("ga4_core", "ga4_channels", ("date",), "sessions"),
        ("ga4_core", "ga4_campaigns", ("date",), "new_users"),
        ("ga4_core", "ga4_landing_pages", ("date",), "sessions"),
        ("search_console_core", "search_console_devices", ("search_type", "date"), "clicks"),
    ],
)
def test_a_breakdown_that_should_sum_to_its_parent_does(
    world: World, parent: str, child: str, keys: tuple[str, ...], column: str
) -> None:
    assert _sums(world, child, keys, column) == pytest.approx(_sums(world, parent, keys, column))


def test_google_ad_groups_and_asset_groups_together_sum_to_the_campaigns(world: World) -> None:
    campaigns = _sums(world, "google_ads_campaigns", ("campaign_id", "date"), "cost")
    parts = _sums(world, "google_ads_ad_groups", ("campaign_id", "date"), "cost")
    for key, value in _sums(world, "google_ads_asset_groups", ("campaign_id", "date"), "cost").items():
        assert key not in parts  # a Performance Max campaign has asset groups, not ad groups
        parts[key] = value
    assert parts == pytest.approx(campaigns)


@pytest.mark.parametrize(
    ("parent", "child", "keys", "column"),
    [
        ("search_console_core", "search_console_queries", ("search_type", "date"), "clicks"),
        ("search_console_core", "search_console_pages", ("search_type", "date"), "clicks"),
        ("search_console_core", "search_console_query_page", ("search_type", "date"), "impressions"),
        ("search_console_core", "search_console_countries", ("search_type", "date"), "clicks"),
        ("google_ads_campaigns", "google_ads_search_terms", ("campaign_id", "date"), "clicks"),
        ("meta_ads_campaigns", "meta_ads_ads", ("campaign_id", "date"), "spend"),
    ],
)
def test_a_grain_that_undercounts_in_real_life_never_overcounts_here(
    world: World, parent: str, child: str, keys: tuple[str, ...], column: str
) -> None:
    whole = _sums(world, parent, keys, column)
    part = _sums(world, child, keys, column)
    assert all(value <= whole[key] for key, value in part.items())
    assert sum(part.values()) < sum(whole.values())


def test_raw_rows_only_use_columns_the_raw_tables_have(world: World) -> None:
    assert set(world.raw) <= set(RAW_TABLES)
    for table, rows in world.raw.items():
        known = {name for name, _ in columns(table)}
        assert all(set(row) <= known for row in rows), table
    assert dict(columns("stripe___invoices"))["paid_at"] == "DateTime64(6, 'UTC')"
    assert dict(columns("linkedin_ads___campaign_groups"))["total_budget"] == "Decimal(38, 9)"


def test_identifiers_are_synthetic(world: World) -> None:
    emails = [row["email"] for row in world.raw["stripe___customers"] + world.raw["twenty___people"]]
    assert emails and all(email.endswith(".example") for email in emails)
    assert all(row["domain"].endswith(".example") for row in world.raw["twenty___companies"])


def test_attribution_resolves_field_by_field_and_some_revenue_has_none(world: World) -> None:
    revenue = world.tables["stripe_revenue"]
    assert any(not row["attributed"] for row in revenue)
    assert any(row["attributed"] and not row["campaign_key"] for row in revenue)  # utm only
    customers = {row["id"]: row for row in world.raw["stripe___customers"]}
    subscriptions = {row["id"]: row for row in world.raw["stripe___subscriptions"]}
    disagree = [
        row
        for row in revenue
        if row["subscription_id"]
        and subscriptions[row["subscription_id"]]["campaign_key"]
        and customers[row["customer_id"]]["campaign_key"]
        not in ("", subscriptions[row["subscription_id"]]["campaign_key"])
    ]
    assert disagree and all(
        row["campaign_key"] == subscriptions[row["subscription_id"]]["campaign_key"] for row in disagree
    )


def test_unpaid_and_deleted_records_stay_out_of_the_agents_view(world: World) -> None:
    invoices = world.raw["stripe___invoices"]
    assert {row["status"] for row in invoices} >= {"paid", "open", "void"}
    paid = [row for row in invoices if row["status"] == "paid" and row["amount_paid"] > 0]
    assert len(world.tables["stripe_revenue"]) == len(paid) < len(invoices)
    deleted = [row for row in world.raw["twenty___opportunities"] if row["deleted_at"].year > 1970]
    assert deleted and len(world.tables["twenty_pipeline"]) == len(world.raw["twenty___opportunities"]) - len(
        deleted
    )


def test_a_note_on_two_records_is_two_activity_rows(world: World) -> None:
    activity = world.tables["twenty_activity"]
    assert len({row["activity_id"] for row in activity}) < len(activity)


def test_periods_know_their_previous_window() -> None:
    assert SEP.days == 30 and SEP.previous() == Period(
        SEP.start.replace(month=8, day=2), SEP.start.replace(month=8, day=31)
    )
    assert SEP.sql() == "date BETWEEN '2026-09-01' AND '2026-09-30'" and END in SEP
