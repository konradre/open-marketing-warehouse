"""The catalog against the ``marts`` views in ``sql/``: what the YAML declares must be what the views hold."""

from __future__ import annotations

from pathlib import Path

from omw.metrics import Catalog
from omw.metrics.ddl import check_views, columns_of, read_views
from omw.metrics.schema import Metric, Report
from omw.pipelines.marts import SQL_DIR


def test_the_catalog_agrees_with_the_views(catalog: Catalog) -> None:
    assert check_views(catalog, SQL_DIR) == []


def test_every_view_is_read_with_its_columns_and_comments() -> None:
    views = read_views(SQL_DIR)
    assert len(views) >= 34 and "marts.meta_ads_ads" in views and "marts.stripe_subscriptions" in views
    campaigns = views["marts.meta_ads_campaigns"]
    assert {"date", "report", "currency", "campaign_id", "spend", "reach"} <= set(campaigns.columns)
    assert "Not additive" in campaigns.columns["reach"]
    assert (
        views["marts.stripe_revenue_daily"].aggregates and views["marts.twenty_pipeline_by_stage"].aggregates
    )
    # A GROUP BY inside a joined subquery does not make the view an aggregate.
    assert (
        not views["marts.meta_ads_campaign_list"].aggregates and not views["marts.stripe_revenue"].aggregates
    )


def test_the_columns_an_expression_reads() -> None:
    assert columns_of("sum(spend) / nullIf(sum(clicks), 0)") == {"spend", "clicks"}
    assert columns_of("uniqExactIf(activity_id, kind = 'task')") == {"activity_id", "kind"}
    assert columns_of("sumIf(net, reporting_category IN ('charge', 'refund'))") == {
        "net",
        "reporting_category",
    }
    assert columns_of("concat('meta_ads:', campaign_id)") == {"campaign_id"}
    assert columns_of("count()") == set()


def _with(catalog: Catalog, **changes: object) -> Catalog:
    """The real catalog with one report or metric replaced (skipping the load-time checks)."""
    reports, metrics = dict(catalog.reports), dict(catalog.metrics)
    for value in changes.values():
        if isinstance(value, Report):
            reports[value.key] = value
        elif isinstance(value, Metric):
            metrics[value.name] = value
    return Catalog.model_construct(reports=reports, metrics=metrics, derived=catalog.derived)


def test_a_sum_over_a_column_the_view_calls_not_additive_is_caught(catalog: Catalog) -> None:
    """The mistake the eval found from the other side: reach and daily users must stay native."""
    for name, column in (("meta_reach", "reach"), ("users", "total_users")):
        summed = catalog.metrics[name].model_copy(update={"aggregation": "sum"})
        problems = check_views(_with(catalog, metric=summed), SQL_DIR)
        assert len(problems) == 1 and f"{name}: {column} is not additive" in problems[0], problems
    daily = catalog.metrics["paying_customers"].model_copy(
        update={"expr": "sum(customers)", "aggregation": "sum", "report": "invoices"}
    )
    report = catalog.reports["stripe.invoices"].model_copy(update={"view": "marts.stripe_revenue_daily"})
    problems = check_views(_with(catalog, metric=daily, report=report), SQL_DIR)
    assert "paying_customers: customers is not additive" in " ".join(problems)
    assert "stripe.invoices: declared row_level but marts.stripe_revenue_daily groups its rows" in problems


def test_a_dimension_or_column_the_view_lacks_is_caught(catalog: Catalog) -> None:
    """A filter on a column the view does not carry would only fail in the warehouse."""
    meta = catalog.reports["meta_ads.campaigns"]
    keyed = meta.model_copy(update={"campaign_key": "campaign_key"})
    assert "meta_ads.campaigns: marts.meta_ads_campaigns has no column 'campaign_key'" in check_views(
        _with(catalog, report=keyed), SQL_DIR
    )
    extra = meta.model_copy(update={"dimensions": {**meta.dimensions, "device": "Device"}})
    assert "meta_ads.campaigns: marts.meta_ads_campaigns has no column 'device'" in check_views(
        _with(catalog, report=extra), SQL_DIR
    )
    typo = catalog.metrics["meta_spend"].model_copy(update={"expr": "sum(spent)"})
    assert check_views(_with(catalog, metric=typo), SQL_DIR) == [
        "meta_spend: marts.meta_ads_campaigns has no column 'spent'"
    ]


def test_dates_and_report_grains_are_checked(catalog: Catalog, tmp_path: Path) -> None:
    subscriptions = catalog.reports["stripe.subscriptions"].model_copy(update={"snapshot": False})
    problems = check_views(_with(catalog, report=subscriptions), SQL_DIR)
    assert (
        "stripe.subscriptions: marts.stripe_subscriptions has no date column; declare it snapshot" in problems
    )
    dated = catalog.reports["ga4.core"].model_copy(update={"snapshot": True})
    assert "ga4.core: declared snapshot but marts.ga4_core has a date column" in check_views(
        _with(catalog, report=dated), SQL_DIR
    )
    wrong = catalog.reports["ga4.core"].model_copy(update={"view": "marts.ga4_channels"})
    assert "ga4.core: marts.ga4_channels does not carry the report grain 'core'" in check_views(
        _with(catalog, report=wrong), SQL_DIR
    )
    assert check_views(catalog, tmp_path)[0] == f"ga4.core: no view marts.ga4_core in {tmp_path.name}/"


def test_every_marts_view_is_read_by_a_report(catalog: Catalog) -> None:
    """Coverage as a check: a new view in sql/ needs a report (and a metric) or a reason here."""
    views = {name for name in read_views(SQL_DIR) if name.startswith("marts.")}
    read = {report.view for report in catalog.reports.values()}
    assert read <= views
    # The two left out are GROUP BY roll-ups of views the catalog reads row by row.
    assert views - read == {"marts.stripe_revenue_daily", "marts.twenty_pipeline_by_stage"}
    used = {metric.report_key for metric in catalog.metrics.values()}
    assert used == set(catalog.reports)
