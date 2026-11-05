"""GA4 and Stripe fixtures loaded into ClickHouse as `pipeline`, read back as `agent`."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import dlt
import pytest
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from omw.pipelines import ga4 as ga4_pipeline
from omw.pipelines import stripe as stripe_pipeline
from tests.integration.conftest import setting
from tests.unit.ga4.conftest import PROPERTY, FakeGA4Client, fixture_rows
from tests.unit.stripe.conftest import FakeStripeClient

pytestmark = pytest.mark.integration

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


def _drop(admin_client: Client, dataset: str) -> None:
    tables = admin_client.query(
        "SELECT database, name, engine FROM system.tables WHERE (database = 'raw' AND name LIKE {p:String}) "
        "OR (database = 'marts' AND name LIKE {p:String})",
        parameters={"p": f"{dataset}%"},
    ).result_rows
    for database, name, engine in tables:
        kind = "VIEW" if engine == "View" else "TABLE"
        admin_client.command(f"DROP {kind} IF EXISTS {database}.`{name}`")


@pytest.fixture(scope="module")
def ga4_loaded(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dlt.Pipeline]:
    _drop(admin_client, "ga4")
    pipeline = dlt.pipeline(
        pipeline_name="ga4_it",
        destination=_destination(),
        dataset_name="ga4",
        pipelines_dir=str(tmp_path_factory.mktemp("dlt")),
    )
    counts = ga4_pipeline.run_daily(pipeline, today=TODAY, property_ids=[PROPERTY], client=FakeGA4Client())
    assert counts["core"] == 3
    yield pipeline
    _drop(admin_client, "ga4")


@pytest.fixture(scope="module")
def stripe_loaded(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dlt.Pipeline]:
    _drop(admin_client, "stripe")
    pipeline = dlt.pipeline(
        pipeline_name="stripe_it",
        destination=_destination(),
        dataset_name="stripe",
        pipelines_dir=str(tmp_path_factory.mktemp("dlt")),
    )
    counts = stripe_pipeline.run_daily(pipeline, today=TODAY, client=FakeStripeClient())
    assert counts["invoices"] == 4
    yield pipeline
    _drop(admin_client, "stripe")


def test_ga4_views_keep_the_other_row_and_sum_to_core(ga4_loaded: dlt.Pipeline, agent_client: Client) -> None:
    for view in ("ga4_core", "ga4_channels", "ga4_landing_pages", "ga4_campaigns"):
        columns = agent_client.query(
            "SELECT name, comment FROM system.columns WHERE database = 'marts' AND table = {t:String}",
            parameters={"t": view},
        ).result_rows
        assert columns and all(comment for _, comment in columns), view
    core: dict[date, int] = {
        day: sessions
        for day, sessions in agent_client.query("SELECT date, sessions FROM marts.ga4_core").result_rows
    }
    channels: dict[date, int] = {
        day: sessions
        for day, sessions in agent_client.query(
            "SELECT date, sum(sessions) FROM marts.ga4_channels GROUP BY date"
        ).result_rows
    }
    assert channels == core
    other = agent_client.query("SELECT count() FROM marts.ga4_channels WHERE is_other").result_rows[0][0]
    assert other == 3


def test_ga4_raw_conventions_and_restatement(
    ga4_loaded: dlt.Pipeline, admin_client: Client, agent_client: Client
) -> None:
    sort_key = admin_client.query(
        "SELECT sorting_key FROM system.tables WHERE database = 'raw' AND name = 'ga4___campaigns'"
    ).result_rows[0][0]
    assert sort_key == "property_id, date, campaign, source_name, medium"
    client = FakeGA4Client()
    client.restated["core"] = fixture_rows("core_restated_2026-10-03")
    ga4_pipeline.run_daily(ga4_loaded, today=TODAY, property_ids=[PROPERTY], client=client)
    rows = agent_client.query("SELECT date, sessions FROM marts.ga4_core ORDER BY date").result_rows
    assert rows == [(date(2026, 10, 1), 1000), (date(2026, 10, 2), 1200), (date(2026, 10, 3), 1010)]
    with pytest.raises(DatabaseError, match="Code: 497"):
        agent_client.query("SELECT count() FROM raw.ga4___core")


def test_stripe_revenue_resolves_attribution_and_converts_amounts(
    stripe_loaded: dlt.Pipeline, agent_client: Client
) -> None:
    rows = agent_client.query(
        "SELECT invoice_id, date, amount, campaign_key, utm_source, attributed FROM marts.stripe_revenue "
        "ORDER BY invoice_id"
    ).result_rows
    assert [(r[0], r[1], Decimal(str(r[2])), r[3], r[4], r[5]) for r in rows] == [
        ("in_T50000000000001", date(2026, 10, 1), Decimal("49"), "meta:23850000000000001", "facebook", True),
        ("in_T50000000000002", date(2026, 10, 2), Decimal("99"), "google:20010002", "google", True),
        ("in_T50000000000003", date(2026, 10, 3), Decimal("250"), "search_console:example", "google", True),
    ]
    daily = agent_client.query(
        "SELECT date, currency, campaign_key, revenue, invoices FROM marts.stripe_revenue_daily ORDER BY date"
    ).result_rows
    assert len(daily) == 3 and Decimal(str(daily[2][3])) == Decimal("250")
    subs = agent_client.query(
        "SELECT subscription_id, campaign_key, unit_amount FROM marts.stripe_subscriptions "
        "ORDER BY subscription_id"
    ).result_rows
    assert subs[0][1] == "meta:23850000000000001" and Decimal(str(subs[1][2])) == Decimal("99")
    for view in ("stripe_revenue", "stripe_revenue_daily", "stripe_subscriptions"):
        columns = agent_client.query(
            "SELECT name, comment FROM system.columns WHERE database = 'marts' AND table = {t:String}",
            parameters={"t": view},
        ).result_rows
        assert columns and all(comment for _, comment in columns), view


def test_stripe_merge_updates_an_invoice_and_appends_only_new_transactions(
    stripe_loaded: dlt.Pipeline, admin_client: Client, agent_client: Client
) -> None:
    client = FakeStripeClient()
    client.overrides = {"invoices": "invoices_updated", "balance_transactions": "balance_transactions_later"}
    counts = stripe_pipeline.run_daily(stripe_loaded, today=TODAY, client=client)
    assert counts.get("balance_transactions", 0) == 1
    assert admin_client.query("SELECT count() FROM raw.stripe___invoices").result_rows[0][0] == 4
    assert admin_client.query("SELECT count() FROM raw.stripe___balance_transactions").result_rows[0][0] == 4
    paid = agent_client.query("SELECT count(), sum(amount) FROM marts.stripe_revenue").result_rows[0]
    assert paid[0] == 4 and Decimal(str(paid[1])) == Decimal("447")
    with pytest.raises(DatabaseError, match="Code: 497"):
        agent_client.query("SELECT count() FROM raw.stripe___customers")
