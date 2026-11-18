"""Distinct counts are served above their own grain when the catalog declares them exact; counts the source
deduplicated per day are still refused. Snapshot reports (entity lists) have no dates."""

from __future__ import annotations

from typing import Any

import pytest

from omw.metrics import Catalog, MetricRequest, Mood, assert_single_select, compile_request
from omw.metrics.moods import ClarifyError, RefuseError
from omw.metrics.runner import answer_request
from omw.metrics.server import list_dimensions_payload, list_metrics_payload
from tests.unit.metrics.conftest import OLD, TODAY


def _execute(sql: str) -> tuple[list[str], list[list[Any]]]:
    if "ops.check_verdicts" in sql:
        return ["source", "check", "report", "status", "detail"], []
    return ["value"], [[7]]


def test_the_declared_distinct_counts_are_the_row_level_ones(catalog: Catalog) -> None:
    distinct = {m.name for m in catalog.metrics.values() if m.aggregation == "distinct"}
    native = {m.name for m in catalog.metrics.values() if m.aggregation == "native"}
    assert distinct == {"paying_customers", "crm_activities", "crm_notes", "crm_tasks"}
    assert native == {"meta_reach", "users", "linkedin_group_daily_budget"}
    for name in distinct:
        assert catalog.report_of(catalog.metrics[name]).row_level, name
    for name in ("meta_reach", "users"):  # daily totals the platform already deduplicated
        assert not catalog.report_of(catalog.metrics[name]).row_level, name


@pytest.mark.parametrize(
    ("metric", "expression", "view"),
    [
        ("paying_customers", "uniqExact(customer_id)", "marts.stripe_revenue"),
        ("crm_tasks", "uniqExactIf(activity_id, kind = 'task')", "marts.twenty_activity"),
        ("crm_notes", "uniqExactIf(activity_id, kind = 'note')", "marts.twenty_activity"),
        ("crm_activities", "uniqExact(activity_id)", "marts.twenty_activity"),
    ],
)
def test_a_distinct_count_is_served_above_its_own_grain(
    catalog: Catalog, metric: str, expression: str, view: str
) -> None:
    """One number for a month: the distinct count is recounted over the month's rows, never summed."""
    answer = answer_request(catalog, MetricRequest(metrics=[metric], time_range=OLD), _execute, TODAY)
    assert answer.mood in (Mood.SERVE, Mood.DISCLOSE), answer.reason
    assert answer.sql.startswith(f"SELECT {expression} AS {metric} FROM {view} WHERE report = ")
    assert "GROUP BY" not in answer.sql and answer.rows == [[7]]
    assert not any("do not add up" in caveat for caveat in answer.caveats)  # one row: nothing to add up


def test_a_distinct_count_at_a_grain_says_its_rows_do_not_add_up(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(metrics=["paying_customers"], dimensions=["date", "currency"], time_range=OLD),
        TODAY,
    )
    assert "GROUP BY date, currency" in plan.sql
    assert (
        "paying_customers is a distinct count, recounted for each row: the rows do not add up to the total"
        in plan.caveats
    )
    with pytest.raises(RefuseError, match="not available at grain 'customer_id'"):
        compile_request(
            catalog,
            MetricRequest(metrics=["paying_customers"], dimensions=["customer_id"], time_range=OLD),
            TODAY,
        )


@pytest.mark.parametrize(
    ("metric", "dimensions"),
    [("meta_reach", []), ("meta_reach", ["date"]), ("users", []), ("users", ["property_id"])],
)
def test_a_count_deduplicated_per_day_by_the_source_is_still_refused(
    catalog: Catalog, metric: str, dimensions: list[str]
) -> None:
    answer = answer_request(
        catalog, MetricRequest(metrics=[metric], dimensions=dimensions, time_range=OLD), _execute, TODAY
    )
    assert answer.mood is Mood.REFUSE and "not additive" in answer.reason and answer.rows == []


def test_list_metrics_tells_the_agent_how_each_metric_aggregates(catalog: Catalog) -> None:
    metrics = {m["name"]: m for m in list_metrics_payload(catalog)["metrics"]}
    assert metrics["paying_customers"]["aggregation"] == "distinct"
    assert metrics["meta_reach"]["aggregation"] == "native" and metrics["meta_spend"]["aggregation"] == "sum"
    assert metrics["meta_daily_budget"]["grains"][0] == "account_id"  # a snapshot has no date grain
    assert metrics["meta_spend"]["grains"][0] == "date"


# ---- snapshots --------------------------------------------------------------------------------------------

SNAPSHOTS = {
    "meta_ads.campaign_list": "marts.meta_ads_campaign_list",
    "meta_ads.ad_set_list": "marts.meta_ads_ad_set_list",
    "google_ads.campaign_list": "marts.google_ads_campaign_list",
    "linkedin_ads.campaign_list": "marts.linkedin_ads_campaign_list",
    "stripe.subscriptions": "marts.stripe_subscriptions",
}


def test_the_entity_lists_are_declared_snapshots(catalog: Catalog) -> None:
    assert {r.key: r.view for r in catalog.reports.values() if r.snapshot} == SNAPSHOTS
    reports = {r["report"]: r for r in list_dimensions_payload(catalog)["reports"]}
    for key in SNAPSHOTS:
        assert reports[key]["snapshot"] is True and "date" not in reports[key]["dimensions"]
    assert (
        reports["meta_ads.campaigns"]["snapshot"] is False
        and "date" in reports["meta_ads.campaigns"]["dimensions"]
    )


def test_a_snapshot_needs_no_time_range_and_has_no_date_filter(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(
            metrics=["meta_daily_budget", "meta_campaigns"],
            dimensions=["campaign_name"],
            filters={"currency": "USD", "status": "ACTIVE"},
        ),
        TODAY,
    )
    assert plan.sql == (
        "SELECT campaign_name, sum(daily_budget) AS meta_daily_budget, count() AS meta_campaigns "
        "FROM marts.meta_ads_campaign_list WHERE currency = 'USD' AND status = 'ACTIVE' "
        "GROUP BY campaign_name ORDER BY campaign_name LIMIT 1000"
    )
    assert plan.comparison is None and assert_single_select(plan.sql)
    assert (
        plan.caveats[0]
        == "meta_ads.campaign_list is the state at the last sync, not a history: it has no dates"
    )
    bare = compile_request(catalog, MetricRequest(metrics=["subscriptions"]), TODAY)
    assert bare.sql == "SELECT count() AS subscriptions FROM marts.stripe_subscriptions ORDER BY 1 LIMIT 1000"


def test_a_time_range_on_a_snapshot_is_said_to_be_ignored(catalog: Catalog) -> None:
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["subscriptions"], filters={"status": "active"}, time_range=OLD),
        _execute,
        TODAY,
    )
    assert answer.mood is Mood.DISCLOSE and "date" not in answer.sql
    assert answer.caveats[0] == (
        "stripe.subscriptions is the state at the last sync, not a history: it has no dates; "
        "the time_range was not applied"
    )


@pytest.mark.parametrize(
    ("request_", "reason"),
    [
        (dict(metrics=["subscriptions"], compare="month"), "no earlier state to compare with"),
        (dict(metrics=["subscriptions"], dimensions=["date"]), "has no dimension 'date'"),
        (dict(metrics=["subscriptions", "paid_invoices"], time_range=OLD), "ask for them separately"),
        (dict(metrics=["google_campaigns", "google_clicks"], time_range=OLD), "ask for them separately"),
        (dict(metrics=["sessions"]), "time_range is required for a dated measure"),
        (dict(metrics=["roas"], filters={"currency": "USD"}), "time_range is required for a dated measure"),
    ],
)
def test_what_a_snapshot_cannot_do_is_refused(
    catalog: Catalog, request_: dict[str, Any], reason: str
) -> None:
    with pytest.raises(RefuseError, match=reason):
        compile_request(catalog, MetricRequest(**request_), TODAY)


def test_budgets_are_money_and_group_budgets_are_per_group_only(catalog: Catalog) -> None:
    with pytest.raises(ClarifyError, match="Which currency"):
        compile_request(catalog, MetricRequest(metrics=["google_daily_budget"]), TODAY)
    grain = ["account_id", "currency", "campaign_group_id", "campaign_group_name"]
    plan = compile_request(
        catalog, MetricRequest(metrics=["linkedin_group_daily_budget"], dimensions=grain), TODAY
    )
    assert "max(group_daily_budget) AS linkedin_group_daily_budget" in plan.sql
    with pytest.raises(RefuseError, match="not additive"):
        compile_request(
            catalog,
            MetricRequest(metrics=["linkedin_group_daily_budget"], dimensions=["currency"]),
            TODAY,
        )


@pytest.mark.parametrize(
    ("metric", "dimension", "view", "report"),
    [
        ("meta_ad_spend", "ad_name", "marts.meta_ads_ads", "ads"),
        ("meta_ad_clicks", "adset_name", "marts.meta_ads_ads", "ads"),
        ("search_device_clicks", "device", "marts.search_console_devices", "devices"),
        ("search_device_impressions", "device", "marts.search_console_devices", "devices"),
        ("search_country_clicks", "country", "marts.search_console_countries", "countries"),
        ("search_country_impressions", "country", "marts.search_console_countries", "countries"),
        ("search_query_page_clicks", "page", "marts.search_console_query_page", "query_page"),
        ("landing_page_sessions", "landing_page", "marts.ga4_landing_pages", "landing_pages"),
        ("landing_page_key_events", "landing_page", "marts.ga4_landing_pages", "landing_pages"),
    ],
)
def test_the_breakdown_grains_added_for_coverage(
    catalog: Catalog, metric: str, dimension: str, view: str, report: str
) -> None:
    dimensions = [dimension, "currency"] if catalog.money(metric) else [dimension]
    plan = compile_request(
        catalog, MetricRequest(metrics=[metric], dimensions=dimensions, time_range=OLD), TODAY
    )
    assert f"FROM {view} WHERE report = '{report}' AND" in plan.sql
    assert f"GROUP BY {', '.join(dimensions)}" in plan.sql
