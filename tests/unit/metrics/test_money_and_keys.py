"""Two rules about what a request may add up or filter on.

Money is never served across currencies: without the ``currency`` dimension or a filter on one currency a
money measure answers ``clarify``. A filter on ``campaign_key`` reads the campaign-key mapping exactly as
the dimension does, on views that have no such column.
"""

from __future__ import annotations

from typing import Any

import pytest

from omw.metrics import Catalog, MetricRequest, Mood, assert_single_select, compile_request
from omw.metrics.moods import ClarifyError, RefuseError
from omw.metrics.runner import answer_request
from tests.unit.metrics.conftest import OLD, TODAY


def _never(sql: str) -> Any:
    raise AssertionError(f"the warehouse must not be asked: {sql}")


MONEY = ["ad_spend", "revenue", "roas", "cac", "pipeline_cac", "cost_per_lead", "blended_cpc", "meta_spend"]
MONEY += ["google_cpc", "linkedin_creative_spend", "net_revenue", "pipeline_amount", "won_amount"]
MONEY += ["meta_daily_budget", "google_daily_budget", "linkedin_daily_budget", "meta_ad_spend"]


@pytest.mark.parametrize("metric", MONEY)
def test_a_money_measure_without_a_currency_is_a_question_back(catalog: Catalog, metric: str) -> None:
    answer = answer_request(catalog, MetricRequest(metrics=[metric], time_range=OLD), _never, TODAY)
    assert answer.mood is Mood.CLARIFY, (metric, answer.reason)
    assert metric in answer.reason and "Which currency?" in answer.reason
    assert [c["name"] for c in answer.candidates] == ["filters.currency", "dimensions.currency"]
    assert all(c["description"] for c in answer.candidates)
    assert answer.rows == [] and answer.sql == ""


def test_every_money_measure_in_the_catalog_is_covered_by_the_rule(catalog: Catalog) -> None:
    """Not a name list: the rule follows the unit, for base metrics and for everything derived from them."""
    names = [*catalog.metrics, *catalog.derived]
    money = [name for name in names if catalog.money(name)]
    assert {m.name for m in catalog.metrics.values() if m.unit == "currency"} <= set(money)
    assert {"roas", "cac", "ad_spend", "blended_cpc", "cost_per_lead", "pipeline_cac"} <= set(money)
    for name in money:
        base = catalog.base_metrics(name)
        native = next((m for m in base if m.aggregation == "native"), None)
        dimensions = [d for d in (native.grains if native else [])]  # a native metric needs its whole grain
        request = MetricRequest(
            metrics=[name],
            dimensions=[d for d in dimensions if d != "currency"],
            time_range=None if catalog.report_of(base[0]).snapshot else OLD,
        )
        with pytest.raises((ClarifyError, RefuseError)):  # never a plan that adds up two currencies
            compile_request(catalog, request, TODAY)
    for name in set(names) - set(money):
        assert not any(m.unit == "currency" for m in catalog.base_metrics(name))


@pytest.mark.parametrize(
    "pin",
    [
        {"filters": {"currency": "USD"}},
        {"filters": {"currency": ["EUR"]}},
        {"dimensions": ["currency"]},
        {"dimensions": ["date", "currency"], "filters": {"currency": ["USD", "EUR"]}},
    ],
)
def test_a_currency_filter_or_dimension_answers(catalog: Catalog, pin: dict[str, Any]) -> None:
    for metric in ("ad_spend", "revenue", "roas", "cac"):
        plan = compile_request(catalog, MetricRequest(metrics=[metric], time_range=OLD, **pin), TODAY)
        assert assert_single_select(plan.sql)
        assert not any("currenc" in caveat for caveat in plan.caveats)


def test_a_filter_on_several_currencies_without_the_dimension_still_mixes(catalog: Catalog) -> None:
    request = MetricRequest(metrics=["revenue"], filters={"currency": ["USD", "EUR"]}, time_range=OLD)
    with pytest.raises(ClarifyError, match="Which currency"):
        compile_request(catalog, request, TODAY)


def test_counts_need_no_currency(catalog: Catalog) -> None:
    for metric in ("meta_clicks", "new_customers", "opportunities", "sessions", "subscriptions"):
        snapshot = catalog.report_of(catalog.metrics[metric]).snapshot
        request = MetricRequest(metrics=[metric], time_range=None if snapshot else OLD)
        assert compile_request(catalog, request, TODAY).sql


@pytest.mark.parametrize(
    ("request_", "reason"),
    [
        (dict(metrics=["roas"], dimensions=["age"]), "cross-source measure"),
        (dict(metrics=["cac"], dimensions=["channel_type"]), "cross-source measure"),
        (dict(metrics=["net_revenue"], dimensions=["campaign_key"]), "has no dimension 'campaign_key'"),
        (dict(metrics=["meta_spend"], dimensions=["device"]), "has no dimension 'device'"),
        (dict(metrics=["meta_spend", "google_spend"], filters={"channel_type": "SEARCH"}), "only one side"),
        (dict(metrics=["linkedin_group_daily_budget"], time_range=None), "not additive"),
    ],
)
def test_a_request_with_no_valid_path_is_refused_not_asked_for_its_currency(
    catalog: Catalog, request_: dict[str, Any], reason: str
) -> None:
    with pytest.raises(RefuseError, match=reason):
        compile_request(catalog, MetricRequest(**{"time_range": OLD, **request_}), TODAY)


def test_an_ambiguous_name_is_asked_before_the_currency(catalog: Catalog) -> None:
    answer = answer_request(catalog, MetricRequest(metrics=["spend"], time_range=OLD), _never, TODAY)
    assert answer.mood is Mood.CLARIFY and "can mean 3 metrics" in answer.reason


# ---- campaign_key as a filter ------------------------------------------------------------------------------

MAPPED = "if(ck.campaign_key != '', ck.campaign_key, concat('meta_ads:', campaign_id))"
JOIN = (
    "LEFT JOIN (SELECT native_id, argMax(campaign_key, updated_at) AS campaign_key FROM marts.campaign_keys "
    "WHERE source = 'meta_ads' GROUP BY native_id) AS ck ON ck.native_id = toString(campaign_id)"
)


def test_a_campaign_key_filter_without_the_dimension_reads_the_mapping(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(
            metrics=["meta_spend"],
            filters={"campaign_key": "widgets_prospecting", "currency": "USD"},
            time_range=OLD,
        ),
        TODAY,
    )
    assert plan.sql.startswith(f"SELECT sum(spend) AS meta_spend FROM marts.meta_ads_campaigns {JOIN} WHERE")
    assert f"AND {MAPPED} = 'widgets_prospecting' AND currency = 'USD'" in plan.sql
    assert " campaign_key = 'widgets_prospecting'" not in plan.sql  # the view has no such column
    assert plan.columns == ["meta_spend"] and assert_single_select(plan.sql)


def test_a_campaign_key_filter_on_a_cross_source_measure_filters_every_side(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(
            metrics=["ad_spend", "roas"],
            filters={"campaign_key": ["widgets_prospecting", "gadgets_launch"], "currency": "USD"},
            time_range=OLD,
        ),
        TODAY,
    )
    keys = "IN ('widgets_prospecting', 'gadgets_launch')"
    for source in ("meta_ads", "google_ads", "linkedin_ads"):
        assert (
            f"if(ck.campaign_key != '', ck.campaign_key, concat('{source}:', campaign_id)) {keys}" in plan.sql
        )
        assert f"WHERE source = '{source}' GROUP BY native_id" in plan.sql
    # Stripe carries the key as a column: filtered directly, no mapping join.
    stripe = plan.sql[plan.sql.index("r_stripe_invoices AS (") :]
    assert f"AND campaign_key {keys} AND currency = 'USD')" in stripe and "LEFT JOIN" not in stripe
    assert plan.sql.count("LEFT JOIN") == 3 and plan.columns == ["ad_spend", "roas"]


def test_the_filter_and_the_dimension_together_use_one_join(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(
            metrics=["meta_spend"],
            dimensions=["campaign_key"],
            filters={"campaign_key": "widgets_prospecting", "currency": "USD"},
            time_range=OLD,
        ),
        TODAY,
    )
    assert plan.sql.startswith(f"SELECT {MAPPED} AS campaign_key, sum(spend) AS meta_spend")
    assert plan.sql.count("LEFT JOIN") == 1 and f"AND {MAPPED} = 'widgets_prospecting'" in plan.sql


def test_a_campaign_key_filter_is_still_refused_where_the_report_has_no_key(catalog: Catalog) -> None:
    with pytest.raises(RefuseError, match="has no dimension 'campaign_key' to filter on"):
        compile_request(
            catalog, MetricRequest(metrics=["sessions"], filters={"campaign_key": "x"}, time_range=OLD), TODAY
        )
    with pytest.raises(RefuseError, match="has no values"):
        compile_request(
            catalog,
            MetricRequest(metrics=["meta_clicks"], filters={"campaign_key": []}, time_range=OLD),
            TODAY,
        )
