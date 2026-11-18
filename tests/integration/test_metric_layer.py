"""Compiled metric queries run as the agent over the fixture loads of Meta, Google Ads, LinkedIn Ads, Stripe
and Twenty, with the post-sync verdicts folded into the caveats."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import pytest
from clickhouse_connect.driver.client import Client

from omw.checks.runner import apply_ops_sql
from omw.metrics import Catalog, MetricRequest, Mood, TimeRange
from omw.metrics.runner import answer_request, clickhouse_executor
from tests.integration.metric_warehouse import OCTOBER, TODAY, loaded

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def warehouse(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Catalog]:
    with loaded(admin_client, tmp_path_factory) as catalog:
        yield catalog


def test_single_source_metrics_run_as_the_agent(warehouse: Catalog, agent_client: Client) -> None:
    execute = clickhouse_executor(agent_client)
    answer = answer_request(
        warehouse,
        MetricRequest(
            metrics=["meta_spend", "meta_clicks", "meta_cpc"],
            dimensions=["date"],
            filters={"currency": "USD"},
            time_range=OCTOBER,
        ),
        execute,
        TODAY,
    )
    assert answer.mood is Mood.DISCLOSE, answer.reason
    assert answer.columns == ["date", "meta_spend", "meta_clicks", "meta_cpc"]
    assert [row[0] for row in answer.rows] == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]
    assert float(answer.rows[0][1]) == 70.60 and answer.rows[0][2] == 390
    assert abs(float(answer.rows[0][3]) - 70.60 / 390) < 1e-6


def test_compare_returns_both_periods(warehouse: Catalog, agent_client: Client) -> None:
    answer = answer_request(
        warehouse,
        MetricRequest(
            metrics=["revenue"],
            filters={"currency": "EUR"},
            time_range=TimeRange(start=date(2026, 10, 3), end=date(2026, 10, 3)),
            compare="previous_period",
        ),
        clickhouse_executor(agent_client),
        TODAY,
    )
    assert answer.mood is not Mood.REFUSE, answer.reason
    by_period = {row[0]: float(row[1]) for row in answer.rows}
    assert by_period == {"current": 250.0, "previous": 99.0}


def test_roas_joins_the_sources_through_the_campaign_key_mapping(
    warehouse: Catalog, agent_client: Client
) -> None:
    answer = answer_request(
        warehouse,
        MetricRequest(
            metrics=["ad_spend", "revenue", "roas"],
            dimensions=["campaign_key"],
            filters={"currency": "EUR"},
            time_range=OCTOBER,
        ),
        clickhouse_executor(agent_client),
        TODAY,
    )
    assert answer.mood is Mood.DISCLOSE, answer.reason
    rows = {row[0]: row[1:] for row in answer.rows}
    google = rows["google:20010002"]
    assert abs(float(google[0]) - (155.25 + 157.25 + 159.25)) < 1e-6  # google_spend only (Meta is in USD)
    assert float(google[1]) == 99.0
    assert abs(float(google[2]) - 99.0 / (155.25 + 157.25 + 159.25)) < 1e-6
    derived = rows["brand_search"]  # Google "Brand - Search": the key the pipeline derived from the name
    assert float(derived[1]) == 0.0 and derived[2] == 0.0  # spend without revenue: ROAS is 0
    assert rows["search_console:example"][2] is None  # revenue without spend
    assert "google_ads:20010001" not in rows


def test_the_pipelines_derive_campaign_keys_and_manual_rows_outrank_them(
    warehouse: Catalog, agent_client: Client
) -> None:
    rows = {
        (r[0], r[1]): (r[2], r[3])
        for r in agent_client.query(
            "SELECT source, native_id, argMax(campaign_key, updated_at), argMax(origin, updated_at) "
            "FROM marts.campaign_keys GROUP BY source, native_id"
        ).result_rows
    }
    assert rows[("meta_ads", "23850000000000002")] == ("widgets_retargeting", "derived")
    assert rows[("google_ads", "20010001")] == ("brand_search", "derived")
    assert rows[("linkedin_ads", "800001")] == ("widgets_sponsored_content", "derived")
    assert rows[("meta_ads", "23850000000000001")] == ("meta:23850000000000001", "manual")


def test_refusals_and_clarifications_never_touch_the_warehouse(
    warehouse: Catalog, agent_client: Client
) -> None:
    calls: list[str] = []
    executor = clickhouse_executor(agent_client)

    def counting(sql: str) -> Any:
        calls.append(sql)
        return executor(sql)

    refused = answer_request(
        warehouse,
        MetricRequest(metrics=["meta_reach"], dimensions=["date"], time_range=OCTOBER),
        counting,
        TODAY,
    )
    clarified = answer_request(
        warehouse, MetricRequest(metrics=["clicks"], time_range=OCTOBER), counting, TODAY
    )
    assert refused.mood is Mood.REFUSE and clarified.mood is Mood.CLARIFY and calls == []


def test_the_guard_and_the_profile_hold_for_compiled_sql(warehouse: Catalog, agent_client: Client) -> None:
    answer = answer_request(
        warehouse,
        MetricRequest(
            metrics=["google_spend"],
            dimensions=["date", "campaign_name"],
            filters={"currency": "EUR"},
            time_range=OCTOBER,
            limit=2,
        ),
        clickhouse_executor(agent_client),
        TODAY,
    )
    assert answer.truncated and len(answer.rows) == 2 and "LIMIT 2" in answer.sql


def test_linkedin_spend_carries_the_campaign_name(warehouse: Catalog, agent_client: Client) -> None:
    answer = answer_request(
        warehouse,
        MetricRequest(
            metrics=["linkedin_spend", "linkedin_clicks"],
            dimensions=["campaign_id", "campaign_name"],
            filters={"currency": "EUR"},
            time_range=OCTOBER,
        ),
        clickhouse_executor(agent_client),
        TODAY,
    )
    assert answer.mood is Mood.DISCLOSE, answer.reason
    rows = {row[0]: row[1:] for row in answer.rows}
    assert set(rows) == {"800001", "800002"} and all(row[0] for row in rows.values())
    assert abs(float(rows["800001"][1]) - (210.40 + 220.40 + 230.40)) < 1e-6


def test_pipeline_cac_divides_ad_spend_by_opportunities_created(
    warehouse: Catalog, agent_client: Client
) -> None:
    answer = answer_request(
        warehouse,
        MetricRequest(
            metrics=["ad_spend", "opportunities", "pipeline_cac"],
            dimensions=["date"],
            filters={"currency": "USD"},
            time_range="last_7_days",
        ),
        clickhouse_executor(agent_client),
        TODAY,
    )
    assert answer.mood is Mood.DISCLOSE, answer.reason
    assert "date BETWEEN '2026-09-28' AND '2026-10-04'" in answer.sql
    rows = {row[0]: row[1:] for row in answer.rows}
    # Meta is the only USD ad platform in the fixtures; one USD opportunity was created on 2026-10-03.
    assert abs(float(rows[date(2026, 10, 3)][0]) - 76.60) < 1e-6
    assert rows[date(2026, 10, 3)][1] == 1 and abs(float(rows[date(2026, 10, 3)][2]) - 76.60) < 1e-6
    assert rows[date(2026, 10, 1)][1] == 0 and rows[date(2026, 10, 1)][2] is None  # join defaults, not NULL


def test_a_failed_check_on_the_source_becomes_a_caveat(
    warehouse: Catalog, admin_client: Client, agent_client: Client
) -> None:
    apply_ops_sql(admin_client)
    admin_client.command("DELETE FROM ops.check_results WHERE source = 'twenty'")
    admin_client.insert(
        "ops.check_results",
        [
            [
                datetime(2026, 10, 5, 6, 0),
                "twenty",
                "freshness",
                "sync",
                "fail",
                100.0,
                30.0,
                "last sync 100.0h ago",
            ],
            [datetime(2026, 10, 5, 6, 0), "twenty", "rowcount", "opportunities", "warn", 0.0, 3.0, "no rows"],
        ],
        column_names=["checked_at", "source", "check", "report", "status", "observed", "expected", "detail"],
    )
    try:
        request = MetricRequest(metrics=["opportunities"], dimensions=["date"], time_range=OCTOBER)
        answer = answer_request(warehouse, request, clickhouse_executor(agent_client), TODAY)
        assert answer.mood is Mood.DISCLOSE
        assert "twenty freshness check fail: last sync 100.0h ago; its numbers may be stale" in answer.caveats
        assert "twenty.opportunities rowcount check warn: no rows" in answer.caveats
        assert answer.rows  # the numbers are still answered
    finally:
        admin_client.command("DELETE FROM ops.check_results WHERE source = 'twenty'")


def test_cost_per_lead_joins_spend_to_leads_by_day(warehouse: Catalog, agent_client: Client) -> None:
    answer = answer_request(
        warehouse,
        MetricRequest(
            metrics=["meta_leads", "ad_spend", "cost_per_lead"],
            dimensions=["date"],
            filters={"currency": "USD"},
            time_range=OCTOBER,
        ),
        clickhouse_executor(agent_client),
        TODAY,
    )
    assert answer.mood is Mood.DISCLOSE, answer.reason
    rows = {row[0]: row[1:] for row in answer.rows}
    assert rows[date(2026, 10, 1)][0] == 1 and abs(float(rows[date(2026, 10, 1)][2]) - 70.60) < 1e-6
    assert rows[date(2026, 10, 3)][0] == 2 and abs(float(rows[date(2026, 10, 3)][2]) - 76.60 / 2) < 1e-6
    clarified = answer_request(
        warehouse,
        MetricRequest(metrics=["leads"], time_range=OCTOBER),
        clickhouse_executor(agent_client),
        TODAY,
    )
    assert clarified.mood is Mood.CLARIFY
    assert [c["name"] for c in clarified.candidates] == ["linkedin_leads", "meta_leads"]
