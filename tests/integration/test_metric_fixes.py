"""The five gaps the evaluation found in the metric layer (eval/README.md), fixed and run as the agent over
the fixture loads: a campaign-key filter, money across currencies, distinct counts above their grain, the
compared window, and the entity lists."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from clickhouse_connect.driver.client import Client

from omw.metrics import Answer, Catalog, MetricRequest, Mood, TimeRange
from omw.metrics.runner import answer_request, clickhouse_executor
from tests.integration.metric_warehouse import OCTOBER, TODAY, loaded

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def warehouse(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Catalog]:
    with loaded(admin_client, tmp_path_factory) as catalog:
        yield catalog


def _ask(catalog: Catalog, client: Client, **request: Any) -> Answer:
    return answer_request(catalog, MetricRequest(**request), clickhouse_executor(client), TODAY)


def _floats(row: list[Any]) -> list[float]:
    return [float(cell) for cell in row]


def test_a_campaign_key_filter_gives_the_row_the_dimension_gives(
    warehouse: Catalog, agent_client: Client
) -> None:
    measures = ["ad_spend", "revenue", "roas"]
    by_key = _ask(
        warehouse,
        agent_client,
        metrics=measures,
        dimensions=["campaign_key"],
        filters={"currency": "EUR"},
        time_range=OCTOBER,
    )
    rows = {row[0]: row[1:] for row in by_key.rows}
    for key in ("google:20010002", "brand_search", "widgets_lead_gen"):  # mapped by hand, derived, LinkedIn
        one = _ask(
            warehouse,
            agent_client,
            metrics=measures,
            filters={"campaign_key": key, "currency": "EUR"},
            time_range=OCTOBER,
        )
        assert one.mood is Mood.DISCLOSE, one.reason  # answered, never a ClickHouse error
        assert _floats(one.rows[0]) == pytest.approx(_floats(rows[key])), key
    meta = _ask(
        warehouse,
        agent_client,
        metrics=["meta_spend"],
        filters={"campaign_key": ["meta:23850000000000001", "widgets_retargeting"], "currency": "USD"},
        time_range=OCTOBER,
    )
    assert float(meta.rows[0][0]) == pytest.approx(151.5 + 69.3)
    none = _ask(
        warehouse,
        agent_client,
        metrics=["meta_clicks"],
        filters={"campaign_key": "no_such_key"},
        time_range=OCTOBER,
    )
    assert none.mood is not Mood.REFUSE and none.rows == [[0]]


def test_money_is_never_added_up_across_currencies(warehouse: Catalog, agent_client: Client) -> None:
    for metric in ("ad_spend", "revenue", "roas", "cac", "pipeline_amount", "google_daily_budget"):
        dated = metric != "google_daily_budget"
        asked = _ask(warehouse, agent_client, metrics=[metric], time_range=OCTOBER if dated else None)
        assert asked.mood is Mood.CLARIFY and asked.rows == [] and asked.sql == "", metric
        assert [c["name"] for c in asked.candidates] == ["filters.currency", "dimensions.currency"]
    split = _ask(warehouse, agent_client, metrics=["ad_spend"], dimensions=["currency"], time_range=OCTOBER)
    assert {row[0]: float(row[1]) for row in split.rows} == pytest.approx({"EUR": 2026.95, "USD": 220.8})
    one = _ask(warehouse, agent_client, metrics=["ad_spend"], filters={"currency": "USD"}, time_range=OCTOBER)
    assert float(one.rows[0][0]) == pytest.approx(220.8)
    assert not any("currenc" in caveat for caveat in [*split.caveats, *one.caveats])


def test_distinct_counts_are_exact_above_their_own_grain(
    warehouse: Catalog, admin_client: Client, agent_client: Client
) -> None:
    year = TimeRange(start=date(2026, 1, 1), end=date(2026, 12, 31))
    rows, activities, tasks = admin_client.query(
        "SELECT count(), uniqExact(activity_id), uniqExactIf(activity_id, kind = 'task') "
        "FROM marts.twenty_activity"
    ).result_rows[0]
    assert rows > activities > tasks > 0  # an activity attached to two records is two rows
    crm = _ask(warehouse, agent_client, metrics=["crm_activities", "crm_tasks", "crm_notes"], time_range=year)
    assert crm.mood is not Mood.REFUSE, crm.reason
    assert crm.rows == [[activities, tasks, activities - tasks]]
    per_record = _ask(
        warehouse, agent_client, metrics=["crm_activities"], dimensions=["company_id"], time_range=year
    )
    assert any("do not add up" in caveat for caveat in per_record.caveats)
    customers = admin_client.query("SELECT uniqExact(customer_id) FROM marts.stripe_revenue").result_rows[0][
        0
    ]
    paying = _ask(warehouse, agent_client, metrics=["paying_customers"], time_range=year)
    assert paying.mood is not Mood.REFUSE and paying.rows == [[customers]] and customers > 0
    for metric in ("meta_reach", "users"):  # daily totals the source deduplicated: still refused
        refused = _ask(warehouse, agent_client, metrics=[metric], time_range=OCTOBER)
        assert refused.mood is Mood.REFUSE and "not additive" in refused.reason


def test_compare_states_its_windows_and_keeps_every_day_of_both(
    warehouse: Catalog, agent_client: Client
) -> None:
    month = _ask(
        warehouse,
        agent_client,
        metrics=["meta_spend"],
        filters={"currency": "USD"},
        time_range=TimeRange(start=date(2026, 11, 1), end=date(2026, 11, 30)),
        compare="month",
    )
    assert month.comparison is not None and month.comparison.model_dump(mode="json") == {
        "compare": "month",
        "current": {"start": "2026-11-01", "end": "2026-11-30", "days": 30},
        "previous": {"start": "2026-10-01", "end": "2026-10-31", "days": 31},
        "overlap_days": 0,
    }
    assert {row[0]: float(row[1]) for row in month.rows} == pytest.approx({"previous": 220.8})
    # Ten days against the ten days a week earlier: 1 to 3 October are in both windows and in both totals.
    week = _ask(
        warehouse,
        agent_client,
        metrics=["meta_spend", "meta_clicks"],
        filters={"currency": "USD"},
        time_range=TimeRange(start=date(2026, 10, 1), end=date(2026, 10, 10)),
        compare="week",
    )
    assert week.mood is Mood.DISCLOSE, week.reason
    assert week.comparison is not None and week.comparison.overlap_days == 3
    assert {row[0]: float(row[1]) for row in week.rows} == pytest.approx(
        {"current": 220.8, "previous": 220.8}
    )
    assert any("share 3 days" in caveat for caveat in week.caveats)


def test_the_entity_lists_answer_as_snapshots(warehouse: Catalog, agent_client: Client) -> None:
    def table(metric: str, label: str, **filters: Any) -> dict[str, float]:
        answer = _ask(warehouse, agent_client, metrics=[metric], dimensions=[label], filters=filters)
        assert answer.mood is Mood.DISCLOSE, answer.reason
        assert "is the state at the last sync, not a history" in answer.caveats[0]
        return {row[0]: float(row[1]) for row in answer.rows}

    assert table("meta_daily_budget", "campaign_name", currency="USD") == {
        "Widgets - Prospecting": 50.0,
        "Widgets - Retargeting": 0.0,
    }
    assert table("google_daily_budget", "campaign_name", currency="EUR") == {
        "Brand - Search": 50.0,
        "Generic - Widgets": 160.0,
        "PMax - Catalog": 100.0,
    }
    assert table("linkedin_daily_budget", "campaign_name", currency="EUR") == {
        "Brand - Text": 20.0,
        "Widgets - Lead Gen": 100.0,
        "Widgets - Sponsored Content": 150.0,
    }
    assert table("subscriptions", "status") == {"active": 2.0, "trialing": 1.0}
    assert table("meta_ad_sets", "status") == {"ACTIVE": 1.0, "PAUSED": 1.0}
    assert table("google_campaigns", "campaign_key") == {
        "brand_search": 1.0,
        "google:20010002": 1.0,
        "pmax_catalog": 1.0,
    }
    groups = _ask(
        warehouse,
        agent_client,
        metrics=["linkedin_group_daily_budget"],
        dimensions=["account_id", "currency", "campaign_group_id", "campaign_group_name"],
    )
    assert {row[3]: float(row[4]) for row in groups.rows} == {"Widgets Q4": 400.0, "Brand": 0.0}
    compared = _ask(warehouse, agent_client, metrics=["subscriptions"], compare="month")
    assert compared.mood is Mood.REFUSE and "no earlier state" in compared.reason


def test_a_report_the_deployment_never_synced_is_refused_in_plain_words(
    warehouse: Catalog, agent_client: Client
) -> None:
    """The fixture load pulls Meta at campaign level only, so marts.meta_ads_ads does not exist."""
    answer = _ask(
        warehouse,
        agent_client,
        metrics=["meta_ad_spend"],
        dimensions=["ad_name"],
        filters={"currency": "USD"},
        time_range=OCTOBER,
    )
    assert answer.mood is Mood.REFUSE and answer.rows == []
    assert answer.reason.startswith("not loaded in this warehouse: meta_ads.ads (view marts.meta_ads_ads).")
    assert "DB::Exception" not in answer.reason and "Code:" not in answer.reason
