"""LinkedIn fixtures loaded through dlt into ClickHouse as `pipeline`, read back as `agent`."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import dlt
import pytest
import responses
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from omw.pipelines.linkedin_ads import run_daily
from omw.sources.linkedin_ads import LinkedInAuth
from tests.integration.conftest import setting
from tests.unit.linkedin_ads.conftest import ACCOUNT, VERSION, register_api

pytestmark = pytest.mark.integration

DATASET = "linkedin_ads"
TODAY = date(2026, 10, 5)


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


def _drop(admin_client: Client) -> None:
    tables = admin_client.query(
        "SELECT database, name, engine FROM system.tables WHERE (database = 'raw' AND name LIKE {p:String}) "
        "OR (database = 'marts' AND name LIKE {p:String})",
        parameters={"p": f"{DATASET}%"},
    ).result_rows
    for database, name, engine in tables:
        kind = "VIEW" if engine == "View" else "TABLE"
        admin_client.command(f"DROP {kind} IF EXISTS {database}.`{name}`")


def _run(pipeline: dlt.Pipeline, analytics: str = "ad_analytics") -> dict[str, int]:
    # responses only intercepts api.linkedin.com; ClickHouse traffic passes through untouched.
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add_passthru("http://127.0.0.1")
        mock.add_passthru("http://localhost")
        register_api(mock, analytics)
        return run_daily(
            pipeline,
            today=TODAY,
            account_ids=[ACCOUNT],
            auth=LinkedInAuth(VERSION, token_provider=lambda: "t"),
        )


@pytest.fixture(scope="module")
def loaded(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dlt.Pipeline]:
    _drop(admin_client)
    pipeline = dlt.pipeline(
        pipeline_name="linkedin_it",
        destination=_destination(),
        dataset_name=DATASET,
        pipelines_dir=str(tmp_path_factory.mktemp("dlt")),
    )
    counts = _run(pipeline)
    assert counts["ad_analytics"] == 6
    yield pipeline
    _drop(admin_client)


def test_agent_reads_the_views_with_budgets_and_cost(loaded: dlt.Pipeline, agent_client: Client) -> None:
    for view in ("linkedin_ads_campaigns", "linkedin_ads_campaign_list"):
        columns = agent_client.query(
            "SELECT name, comment FROM system.columns WHERE database = 'marts' AND table = {t:String}",
            parameters={"t": view},
        ).result_rows
        assert columns and all(comment for _, comment in columns), view
    rows = agent_client.query(
        f"SELECT campaign_id, date, cost, currency, conversions FROM marts.{DATASET}_campaigns "
        "ORDER BY campaign_id, date"
    ).result_rows
    assert (
        len(rows) == 6
        and rows[0][0] == "800001"
        and Decimal(str(rows[0][2])) == Decimal("210.40")
        and rows[0][3] == "EUR"
    )
    listing = {
        r[0]: r
        for r in agent_client.query(
            "SELECT campaign_id, campaign_group_name, group_daily_budget, group_budget_optimization, "
            "daily_budget, cost_type "
            f"FROM marts.{DATASET}_campaign_list"
        ).result_rows
    }
    assert listing["800001"][1] == "Widgets Q4" and Decimal(str(listing["800001"][2])) == Decimal("400")
    assert (
        listing["800001"][3] is True
        and Decimal(str(listing["800001"][4])) == Decimal("150")
        and listing["800001"][5] == "CPM"
    )
    assert listing["800003"][3] is False and Decimal(str(listing["800003"][2])) == Decimal("0")


def test_creatives_resolve_their_campaign_and_never_exceed_it(
    loaded: dlt.Pipeline, agent_client: Client
) -> None:
    rows = agent_client.query(
        "SELECT cr.date, cr.campaign_id, cr.campaign_name, cr.creative_id, cr.is_serving, cr.cost, "
        f"cr.clicks, c.cost, c.clicks FROM marts.{DATASET}_creatives AS cr "
        f"JOIN marts.{DATASET}_campaigns AS c ON c.date = cr.date AND c.campaign_id = cr.campaign_id "
        "ORDER BY cr.date, cr.creative_id"
    ).result_rows
    assert len(rows) == 6 and rows[0][1] == "800001" and rows[0][3] == "900001" and rows[0][2]
    assert rows[0][4] is True and Decimal(str(rows[0][5])) == Decimal("189.36")
    for row in rows:
        assert Decimal(str(row[5])) <= Decimal(str(row[7])) and row[6] <= row[8]
    columns = agent_client.query(
        "SELECT name, comment FROM system.columns WHERE database = 'marts' AND table = {t:String}",
        parameters={"t": f"{DATASET}_creatives"},
    ).result_rows
    assert columns and all(comment for _, comment in columns)


def test_raw_conventions_and_invisibility(
    loaded: dlt.Pipeline, admin_client: Client, agent_client: Client
) -> None:
    sort_key = admin_client.query(
        "SELECT sorting_key FROM system.tables WHERE database = 'raw' "
        "AND name = 'linkedin_ads___ad_analytics'"
    ).result_rows[0][0]
    assert sort_key == "account_id, date, campaign_id"
    with pytest.raises(DatabaseError, match="Code: 497"):
        agent_client.query(f"SELECT count() FROM raw.{DATASET}___campaigns")


def test_restated_day_is_replaced_and_entities_merge(
    loaded: dlt.Pipeline, admin_client: Client, agent_client: Client
) -> None:
    _run(loaded, analytics="ad_analytics_restated_2026-10-02")
    rows = agent_client.query(
        f"SELECT date, count(), sum(cost) FROM marts.{DATASET}_campaigns GROUP BY date ORDER BY date"
    ).result_rows
    assert [(d, n) for d, n, _ in rows] == [
        (date(2026, 10, 1), 2),
        (date(2026, 10, 2), 2),
        (date(2026, 10, 3), 2),
    ]
    assert Decimal(str(rows[1][2])) == Decimal("375.60")
    assert admin_client.query("SELECT count() FROM raw.linkedin_ads___campaigns").result_rows[0][0] == 3
