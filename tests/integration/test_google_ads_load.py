"""Google Ads fixtures loaded through dlt into ClickHouse as `pipeline`, read back as `agent`."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import dlt
import pytest
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from omw.pipelines.google_ads import run_daily
from tests.integration.conftest import setting
from tests.unit.google_ads.conftest import CUSTOMER, FakeGoogleAdsClient, fixture_results

pytestmark = pytest.mark.integration

DATASET = "google_ads"
TODAY = date(2026, 10, 5)  # 30-day window ends 2026-10-04 and covers the fixture days 10-01..03


def _destination() -> Any:
    return dlt.destinations.clickhouse(
        credentials={
            "host": setting("OMW_CLICKHOUSE_HOST"),
            "port": int(setting("OMW_CLICKHOUSE_NATIVE_PORT")),
            "http_port": int(setting("OMW_CLICKHOUSE_HTTP_PORT")),
            "username": "pipeline",
            "password": setting("OMW_PIPELINE_PASSWORD"),
            "database": "raw",
            "secure": 0,
        }
    )


def _drop_everything(admin_client: Client) -> None:
    tables = admin_client.query(
        "SELECT database, name, engine FROM system.tables WHERE (database = 'raw' AND name LIKE {p:String}) "
        "OR (database = 'marts' AND name LIKE {p:String})",
        parameters={"p": f"{DATASET}%"},
    ).result_rows
    for database, name, engine in tables:
        kind = "VIEW" if engine == "View" else "TABLE"
        admin_client.command(f"DROP {kind} IF EXISTS {database}.`{name}`")


@pytest.fixture(scope="module")
def loaded(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dlt.Pipeline]:
    _drop_everything(admin_client)
    pipeline = dlt.pipeline(
        pipeline_name="google_ads_it",
        destination=_destination(),
        dataset_name=DATASET,
        pipelines_dir=str(tmp_path_factory.mktemp("dlt")),
    )
    counts = run_daily(pipeline, today=TODAY, customer_ids=[CUSTOMER], client=FakeGoogleAdsClient())
    assert counts["performance_campaigns"] == 9
    yield pipeline
    _drop_everything(admin_client)


def test_raw_tables_follow_the_conventions(loaded: dlt.Pipeline, admin_client: Client) -> None:
    expected = {
        "performance_campaigns": "customer_id, date, campaign_id",
        "performance_ad_groups": "customer_id, date, campaign_id, ad_group_id",
        "campaigns": "campaign_id",
    }
    for table, sort_key in expected.items():
        rows = admin_client.query(
            "SELECT sorting_key FROM system.tables WHERE database = 'raw' AND name = {t:String}",
            parameters={"t": f"{DATASET}___{table}"},
        ).result_rows
        assert rows and rows[0][0] == sort_key, table
        types = admin_client.query(
            "SELECT name, type FROM system.columns WHERE database = 'raw' AND table = {t:String}",
            parameters={"t": f"{DATASET}___{table}"},
        ).result_rows
        assert not [c for c, t in types if "Nullable" in t and not c.startswith("_dlt")], (table, types)


def test_agent_reads_cost_in_currency_through_commented_views(
    loaded: dlt.Pipeline, agent_client: Client
) -> None:
    for view in ("google_ads_campaigns", "google_ads_ad_groups", "google_ads_campaign_list"):
        columns = agent_client.query(
            "SELECT name, type, comment FROM system.columns WHERE database = 'marts' AND table = {t:String}",
            parameters={"t": view},
        ).result_rows
        assert columns and all(comment for _, _, comment in columns), [c for c, _, m in columns if not m]
    row = agent_client.query(
        f"SELECT currency, cost, cost_micros FROM marts.{DATASET}_campaigns "
        "WHERE date = '2026-10-01' AND campaign_id = '20010001'"
    ).result_rows[0]
    assert row[0] == "EUR" and Decimal(str(row[1])) == Decimal("48.5") and row[2] == 48_500_000
    types = {
        c: t
        for c, t, _ in agent_client.query(
            "SELECT name, type, comment FROM system.columns "
            "WHERE database = 'marts' AND table = 'google_ads_campaigns'"
        ).result_rows
    }
    assert types["cost"] == "Decimal(18, 6)" and types["customer_id"] == "LowCardinality(String)"
    assert agent_client.query(f"SELECT count() FROM marts.{DATASET}_campaign_list").result_rows[0][0] == 3
    budget = agent_client.query(
        f"SELECT daily_budget FROM marts.{DATASET}_campaign_list WHERE campaign_id = '20010002'"
    ).result_rows[0][0]
    assert Decimal(str(budget)) == Decimal("160")


def test_ad_groups_sum_to_campaigns_in_marts(loaded: dlt.Pipeline, agent_client: Client) -> None:
    campaigns: dict[tuple[str, date], int] = {
        key: cost
        for key, cost in agent_client.query(
            f"SELECT (campaign_id, date), sum(cost_micros) FROM marts.{DATASET}_campaigns "
            "GROUP BY campaign_id, date"
        ).result_rows
    }
    ad_groups = agent_client.query(
        f"SELECT (campaign_id, date), sum(cost_micros) FROM marts.{DATASET}_ad_groups "
        "GROUP BY campaign_id, date"
    ).result_rows
    assert ad_groups
    for key, cost in ad_groups:
        assert cost == campaigns[key], key


def test_search_terms_never_exceed_their_campaign(loaded: dlt.Pipeline, agent_client: Client) -> None:
    terms = agent_client.query(
        "SELECT date, campaign_id, sum(clicks), sum(cost_micros), count() FROM marts.google_ads_search_terms "
        "GROUP BY date, campaign_id ORDER BY date"
    ).result_rows
    assert len(terms) == 3 and all(r[4] == 2 for r in terms)
    for day, campaign_id, clicks, cost_micros, _ in terms:
        campaign = agent_client.query(
            "SELECT clicks, cost_micros FROM marts.google_ads_campaigns "
            "WHERE date = {d:Date} AND campaign_id = {c:String}",
            parameters={"d": day, "c": campaign_id},
        ).result_rows[0]
        assert clicks <= campaign[0] and cost_micros <= campaign[1]
    columns = agent_client.query(
        "SELECT name, comment FROM system.columns "
        "WHERE database = 'marts' AND table = 'google_ads_search_terms'"
    ).result_rows
    assert columns and all(comment for _, comment in columns)


def test_asset_groups_sum_to_their_performance_max_campaign(
    loaded: dlt.Pipeline, agent_client: Client
) -> None:
    groups = agent_client.query(
        "SELECT date, campaign_id, sum(clicks), sum(cost_micros), count() FROM marts.google_ads_asset_groups "
        "GROUP BY date, campaign_id ORDER BY date"
    ).result_rows
    assert len(groups) == 3 and all(r[1] == "20010003" and r[4] == 2 for r in groups)
    for day, campaign_id, clicks, cost_micros, _ in groups:
        campaign = agent_client.query(
            "SELECT clicks, cost_micros, channel_type FROM marts.google_ads_campaigns "
            "WHERE date = {d:Date} AND campaign_id = {c:String}",
            parameters={"d": day, "c": campaign_id},
        ).result_rows[0]
        assert campaign[2] == "PERFORMANCE_MAX" and clicks <= campaign[0] and cost_micros <= campaign[1]
        assert clicks >= campaign[0] - 2 and cost_micros >= campaign[1] - 2  # integer shares of the campaign


def test_raw_stays_invisible_to_the_agent(loaded: dlt.Pipeline, agent_client: Client) -> None:
    with pytest.raises(DatabaseError, match="Code: 497"):
        agent_client.query(f"SELECT count() FROM raw.{DATASET}___performance_campaigns")


def test_restated_days_are_replaced_not_duplicated(loaded: dlt.Pipeline, agent_client: Client) -> None:
    client = FakeGoogleAdsClient()
    client.restated = {"2026-10-02": fixture_results("campaigns_restated_2026-10-02")}
    run_daily(loaded, today=TODAY, customer_ids=[CUSTOMER], client=client)
    rows = agent_client.query(
        f"SELECT date, count(), sum(conversions) FROM marts.{DATASET}_campaigns GROUP BY date ORDER BY date"
    ).result_rows
    assert [(d, n) for d, n, _ in rows] == [
        (date(2026, 10, 1), 3),
        (date(2026, 10, 2), 3),
        (date(2026, 10, 3), 3),
    ]
    assert rows[1][2] == 50.0  # 15 + 11 + 24 after the restatement
