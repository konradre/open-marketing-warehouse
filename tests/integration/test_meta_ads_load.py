"""Meta Ads fixtures loaded through dlt into ClickHouse as `pipeline`, read back as `agent`; ops events."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import dlt
import pytest
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from omw.pacing import MetaPacer
from omw.pipelines.meta_ads import run
from tests.integration.conftest import setting
from tests.unit.meta_ads.conftest import ACCOUNT, FakeAccount, fixture_rows

pytestmark = pytest.mark.integration

DATASET = "meta_ads"
TODAY = date(2026, 10, 4)  # daily window 2026-09-27..10-03 covers the fixture days


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
    admin_client.command("DROP TABLE IF EXISTS ops.pacing_events")


@pytest.fixture(scope="module")
def loaded(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dlt.Pipeline]:
    _drop_everything(admin_client)
    pipeline = dlt.pipeline(
        pipeline_name="meta_ads_it",
        destination=_destination(),
        dataset_name=DATASET,
        pipelines_dir=str(tmp_path_factory.mktemp("dlt")),
    )
    pacer = MetaPacer(sleep=lambda _: None)
    result = run(
        pipeline,
        "daily",
        [ACCOUNT],
        levels=["campaign", "ad"],
        breakdowns=["age_gender", "countries", "placements"],
        today=TODAY,
        pacer=pacer,
        accounts={ACCOUNT: FakeAccount(pacer=pacer)},
    )
    assert result.counts["insights_campaigns"] == 6 and result.counts["insights_ads"] == 6
    assert result.counts["insights_age_gender"] == 12 and result.counts["insights_countries"] == 4
    yield pipeline
    _drop_everything(admin_client)


def test_raw_tables_follow_the_conventions(loaded: dlt.Pipeline, admin_client: Client) -> None:
    expected = {
        "insights_campaigns": "account_id, date, campaign_id",
        "insights_ads": "account_id, date, campaign_id, adset_id, ad_id",
        "campaigns": "id",
    }
    for table, sort_key in expected.items():
        rows = admin_client.query(
            "SELECT sorting_key FROM system.tables WHERE database = 'raw' AND name = {t:String}",
            parameters={"t": f"{DATASET}___{table}"},
        ).result_rows
        assert rows and rows[0][0] == sort_key, table
    types = admin_client.query(
        "SELECT name, type FROM system.columns WHERE database = 'raw' AND table = {t:String}",
        parameters={"t": f"{DATASET}___insights_campaigns"},
    ).result_rows
    assert not [c for c, t in types if "Nullable" in t and not c.startswith("_dlt")], types


def test_agent_reads_both_grains_through_commented_views(loaded: dlt.Pipeline, agent_client: Client) -> None:
    for grain in ("campaigns", "ads"):
        view = f"marts.{DATASET}_{grain}"
        count, reports = agent_client.query(f"SELECT count(), uniqExact(report) FROM {view}").result_rows[0]
        assert count == 6 and reports == 1, view
        columns = agent_client.query(
            "SELECT name, type, comment FROM system.columns WHERE database = 'marts' AND table = {t:String}",
            parameters={"t": f"{DATASET}_{grain}"},
        ).result_rows
        assert columns and all(comment for _, _, comment in columns), [c for c, _, m in columns if not m]
        by_name = {c: t for c, t, _ in columns}
        assert by_name["spend"].startswith("Decimal") and by_name["account_id"] == "LowCardinality(String)"
    spend = agent_client.query(
        f"SELECT sum(spend) FROM marts.{DATASET}_campaigns WHERE date = '2026-10-01'"
    ).result_rows[0][0]
    assert Decimal(str(spend)) == Decimal("70.60")


def test_entity_lists_carry_budgets_in_the_account_currency(
    loaded: dlt.Pipeline, agent_client: Client
) -> None:
    campaigns = {
        r[0]: r[1:]
        for r in agent_client.query(
            "SELECT campaign_name, currency, daily_budget, lifetime_budget, status, stop_time "
            f"FROM marts.{DATASET}_campaign_list"
        ).result_rows
    }
    prospecting = campaigns["Widgets - Prospecting"]
    assert prospecting[0] == "USD" and Decimal(str(prospecting[1])) == Decimal("50")  # "5000" cents
    assert Decimal(str(prospecting[2])) == Decimal("0") and prospecting[3] == "ACTIVE"
    assert prospecting[4] == datetime(1970, 1, 1)  # open-ended: the optional time is absent in the fixture
    ad_sets = {
        r[0]: r[1:]
        for r in agent_client.query(
            "SELECT adset_name, campaign_name, currency, bid_amount, daily_budget "
            f"FROM marts.{DATASET}_ad_set_list"
        ).result_rows
    }
    assert (
        ad_sets["Prospecting - Broad"][0] == "Widgets - Prospecting"
        and ad_sets["Prospecting - Broad"][1] == "USD"
    )
    for view in ("campaign_list", "ad_set_list"):
        columns = agent_client.query(
            "SELECT name, comment FROM system.columns WHERE database = 'marts' AND table = {t:String}",
            parameters={"t": f"{DATASET}_{view}"},
        ).result_rows
        assert columns and all(comment for _, comment in columns), view


def test_breakdowns_sum_to_campaigns_on_spend_and_clicks(loaded: dlt.Pipeline, agent_client: Client) -> None:
    for view in ("age_gender", "countries", "placements"):
        rows = agent_client.query(
            f"SELECT b.date, b.campaign_id, sum(b.spend), sum(b.clicks), any(c.spend), any(c.clicks) "
            f"FROM marts.{DATASET}_{view} AS b "
            f"JOIN marts.{DATASET}_campaigns AS c ON c.date = b.date AND c.campaign_id = b.campaign_id "
            "GROUP BY b.date, b.campaign_id"
        ).result_rows
        assert rows, view
        for _, _, spend, clicks, campaign_spend, campaign_clicks in rows:
            assert Decimal(str(spend)) <= Decimal(str(campaign_spend)) and clicks <= campaign_clicks
            assert Decimal(str(spend)) >= Decimal(str(campaign_spend)) - Decimal("0.02")
        columns = agent_client.query(
            "SELECT name, comment FROM system.columns WHERE database = 'marts' AND table = {t:String}",
            parameters={"t": f"{DATASET}_{view}"},
        ).result_rows
        assert columns and all(comment for _, comment in columns), view


def test_raw_stays_invisible_to_the_agent(loaded: dlt.Pipeline, agent_client: Client) -> None:
    with pytest.raises(DatabaseError, match="Code: 497"):
        agent_client.query(f"SELECT count() FROM raw.{DATASET}___insights_campaigns")
    with pytest.raises(DatabaseError, match="Code: 497"):
        agent_client.query("SELECT count() FROM ops.pacing_events")


def test_restated_days_are_replaced_not_duplicated(loaded: dlt.Pipeline, agent_client: Client) -> None:
    pacer = MetaPacer(sleep=lambda _: None)
    restated = {"2026-10-02": fixture_rows("insights_campaigns_restated_2026-10-02")}
    run(
        loaded,
        "daily",
        [ACCOUNT],
        today=TODAY,
        pacer=pacer,
        accounts={ACCOUNT: FakeAccount(pacer=pacer, restated=restated)},
    )
    rows = agent_client.query(
        f"SELECT date, count(), sum(spend) FROM marts.{DATASET}_campaigns GROUP BY date ORDER BY date"
    ).result_rows
    assert [(d, n) for d, n, _ in rows] == [
        (date(2026, 10, 1), 2),
        (date(2026, 10, 2), 2),
        (date(2026, 10, 3), 2),
    ]
    assert Decimal(str(rows[1][2])) == Decimal("75.15")


def test_a_stopped_run_keeps_complete_days_and_records_the_reason(
    loaded: dlt.Pipeline, admin_client: Client, agent_client: Client
) -> None:
    pacer = MetaPacer(sleep=lambda _: None)
    account = FakeAccount(pacer=pacer, stop_on=date(2026, 10, 3))
    result = run(loaded, "weekly", [ACCOUNT], today=TODAY, pacer=pacer, accounts={ACCOUNT: account})
    assert result.stopped is not None and result.counts.get("insights_campaigns") == 4
    assert agent_client.query(f"SELECT count() FROM marts.{DATASET}_campaigns").result_rows[0][0] == 6
    events = admin_client.query(
        "SELECT source, account_id, mode, event, reason, utilisation FROM ops.pacing_events "
        "ORDER BY recorded_at"
    ).result_rows
    assert events == [("meta_ads", ACCOUNT, "weekly", "stopped", "utilisation", 96.0)]
