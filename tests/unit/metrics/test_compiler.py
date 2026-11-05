"""The SQL the compiler emits, by shape."""

from __future__ import annotations

from datetime import date

import pytest

from omw.metrics import Catalog, MetricRequest, TimeRange, assert_single_select, compile_request
from omw.metrics.moods import RefuseError
from tests.unit.metrics.conftest import OCTOBER, TODAY


def test_single_report_groups_and_filters(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(
            metrics=["meta_spend", "meta_cpc"],
            dimensions=["date", "campaign_name"],
            filters={"account_id": "100000000000001"},
            time_range=OCTOBER,
        ),
        TODAY,
    )
    sql = plan.sql
    assert sql.startswith(
        "SELECT date, campaign_name, sum(spend) AS meta_spend, "
        "sum(spend) / nullIf(sum(clicks), 0) AS meta_cpc"
    )
    assert "FROM marts.meta_ads_campaigns WHERE report = 'campaigns'" in sql
    assert "(date BETWEEN '2026-10-01' AND '2026-10-03')" in sql and "account_id = '100000000000001'" in sql
    assert sql.endswith("GROUP BY date, campaign_name ORDER BY date, campaign_name LIMIT 1000")
    assert plan.columns == ["date", "campaign_name", "meta_spend", "meta_cpc"]
    assert assert_single_select(sql)


def test_list_filters_and_escaping(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(
            metrics=["revenue"],
            filters={"utm_source": ["google", "it's"], "attributed": True},
            time_range=OCTOBER,
        ),
        TODAY,
    )
    assert "utm_source IN ('google', 'it\\'s')" in plan.sql and "attributed = 1" in plan.sql
    assert plan.sql.endswith("ORDER BY 1 LIMIT 1000")


def test_compare_adds_a_period_column(catalog: Catalog) -> None:
    plan = compile_request(
        catalog, MetricRequest(metrics=["sessions"], time_range=OCTOBER, compare="week"), TODAY
    )
    assert "if(date BETWEEN '2026-10-01' AND '2026-10-03', 'current', 'previous') AS period" in plan.sql
    assert "(date BETWEEN '2026-09-24' AND '2026-09-26')" in plan.sql
    assert "GROUP BY period ORDER BY period" in plan.sql
    assert plan.columns == ["period", "sessions"]


@pytest.mark.parametrize(
    ("compare", "start", "end"),
    [
        ("previous_period", date(2026, 9, 28), date(2026, 9, 30)),
        ("week", date(2026, 9, 24), date(2026, 9, 26)),
        ("month", date(2026, 9, 1), date(2026, 9, 3)),
        ("year", date(2025, 10, 1), date(2025, 10, 3)),
    ],
)
def test_comparison_periods(compare: str, start: date, end: date) -> None:
    assert OCTOBER.shifted(compare) == TimeRange(start=start, end=end)  # type: ignore[arg-type]
    assert TimeRange(start=date(2026, 3, 31), end=date(2026, 3, 31)).shifted("month") == TimeRange(
        start=date(2026, 2, 28), end=date(2026, 2, 28)
    )


def test_cross_source_measure_is_one_cte_per_source_joined_on_the_common_grain(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(
            metrics=["roas", "ad_spend"],
            dimensions=["date", "campaign_key"],
            filters={"currency": "eur"},
            time_range=OCTOBER,
        ),
        TODAY,
    )
    sql = plan.sql
    assert sql.startswith(
        "WITH r_google_ads_campaigns AS (SELECT date, "
        "if(ck.campaign_key != '', ck.campaign_key, concat('google_ads:', campaign_id)) AS campaign_key"
    )
    assert (
        "LEFT JOIN marts.campaign_keys AS ck ON ck.source = 'meta_ads' "
        "AND ck.native_id = toString(campaign_id)" in sql
    )
    assert (
        "r_stripe_invoices AS (SELECT date, campaign_key, sum(amount) AS revenue FROM marts.stripe_revenue"
        in sql
    )
    assert sql.count("currency = 'eur'") == 3
    assert (
        "FULL OUTER JOIN r_meta_ads_campaigns USING (date, campaign_key) "
        "FULL OUTER JOIN r_stripe_invoices USING (date, campaign_key)" in sql
    )
    assert "toFloat64(revenue) / nullIf((toFloat64(meta_spend) + toFloat64(google_spend)), 0) AS roas" in sql
    assert "toFloat64(meta_spend) + toFloat64(google_spend) AS ad_spend" in sql
    assert plan.columns == ["date", "campaign_key", "roas", "ad_spend"]
    assert "SELECT date, campaign_key, toFloat64(revenue) / nullIf(" in plan.sql
    assert [r.report.key for r in plan.reports] == [
        "google_ads.campaigns",
        "meta_ads.campaigns",
        "stripe.invoices",
    ]


def test_two_sources_without_a_derived_measure_still_join_on_shared_dimensions(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(metrics=["meta_spend", "google_spend"], dimensions=["date"], time_range=OCTOBER),
        TODAY,
    )
    assert "FULL OUTER JOIN r_google_ads_campaigns USING (date)" in plan.sql
    assert plan.columns == ["date", "meta_spend", "google_spend"]


def test_derived_on_one_source_wraps_the_cte(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(metrics=["meta_spend", "meta_clicks"], dimensions=["date"], time_range=OCTOBER),
        TODAY,
    )
    assert plan.sql.startswith("SELECT date, sum(spend)")


@pytest.mark.parametrize(
    ("request_", "reason"),
    [
        (dict(metrics=["meta_reach"], dimensions=["date"]), "not additive"),
        (dict(metrics=["users"], dimensions=[]), "not additive"),
        (dict(metrics=["search_clicks"], dimensions=["query"]), "has no dimension 'query'"),
        (
            dict(metrics=["paying_customers"], dimensions=["date", "customer_id"]),
            "not available at grain 'customer_id'",
        ),
        (dict(metrics=["meta_spend"], dimensions=["channel_type"]), "has no dimension 'channel_type'"),
        (dict(metrics=["roas"], dimensions=["campaign_name"]), "cross-source measure"),
        (dict(metrics=["roas"], filters={"campaign_name": "x"}), "cannot be filtered by 'campaign_name'"),
        (
            dict(metrics=["meta_spend", "google_spend"], dimensions=["channel_type"]),
            "exists on only one side",
        ),
        (
            dict(metrics=["meta_spend", "google_spend"], filters={"account_id": "1"}),
            "exists on only one side",
        ),
        (dict(metrics=["nope"]), "unknown metric"),
        (dict(metrics=["meta_spend"], dimensions=["date; drop"]), "not a valid column name"),
    ],
)
def test_refusals(catalog: Catalog, request_: dict[str, object], reason: str) -> None:
    with pytest.raises(RefuseError, match=reason):
        compile_request(catalog, MetricRequest(time_range=OCTOBER, **request_), TODAY)  # type: ignore[arg-type]
