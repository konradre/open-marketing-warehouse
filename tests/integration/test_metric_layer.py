"""Compiled metric queries run as the agent over the fixture loads of Meta, Google Ads and Stripe."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import dlt
import pytest
from clickhouse_connect.driver.client import Client

from omw.metrics import Catalog, MetricRequest, Mood, TimeRange, load_catalog
from omw.metrics.runner import answer_request, clickhouse_executor
from omw.pacing import MetaPacer
from omw.pipelines import google_ads as google_pipeline
from omw.pipelines import meta_ads as meta_pipeline
from omw.pipelines import stripe as stripe_pipeline
from omw.pipelines.marts import SQL_DIR, statements
from tests.integration.conftest import setting
from tests.unit.google_ads.conftest import CUSTOMER, FakeGoogleAdsClient
from tests.unit.meta_ads.conftest import ACCOUNT, FakeAccount
from tests.unit.stripe.conftest import FakeStripeClient

pytestmark = pytest.mark.integration

TODAY = date(2026, 10, 5)
OCTOBER = TimeRange(start=date(2026, 10, 1), end=date(2026, 10, 3))


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
def warehouse(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Catalog]:
    for dataset in ("meta_ads", "google_ads", "stripe"):
        _drop(admin_client, dataset)
    admin_client.command("DROP TABLE IF EXISTS marts.campaign_keys")
    for statement in statements((SQL_DIR / "60_metric_layer.sql").read_text()):
        admin_client.command(statement)
    pacer = MetaPacer(sleep=lambda _: None)
    meta_pipeline.run(
        dlt.pipeline(
            "meta_ads_ml",
            destination=_destination(),
            dataset_name="meta_ads",
            pipelines_dir=str(tmp_path_factory.mktemp("m")),
        ),
        "daily",
        [ACCOUNT],
        today=TODAY,
        pacer=pacer,
        accounts={ACCOUNT: FakeAccount(pacer=pacer)},
        record_ops=False,
    )
    google_pipeline.run_daily(
        dlt.pipeline(
            "google_ads_ml",
            destination=_destination(),
            dataset_name="google_ads",
            pipelines_dir=str(tmp_path_factory.mktemp("g")),
        ),
        today=TODAY,
        customer_ids=[CUSTOMER],
        client=FakeGoogleAdsClient(),
    )
    stripe_pipeline.run_daily(
        dlt.pipeline(
            "stripe_ml",
            destination=_destination(),
            dataset_name="stripe",
            pipelines_dir=str(tmp_path_factory.mktemp("s")),
        ),
        today=TODAY,
        client=FakeStripeClient(),
    )
    # Map one Meta and one Google campaign onto the keys the Stripe fixtures carry in their metadata.
    admin_client.insert(
        "marts.campaign_keys",
        [
            ["meta_ads", "23850000000000001", "meta:23850000000000001", "Widgets - Prospecting"],
            ["google_ads", "20010002", "google:20010002", "Generic - Widgets"],
        ],
        column_names=["source", "native_id", "campaign_key", "campaign_name"],
    )
    yield load_catalog()
    for dataset in ("meta_ads", "google_ads", "stripe"):
        _drop(admin_client, dataset)
    admin_client.command("DROP TABLE IF EXISTS marts.campaign_keys")


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
    unmapped = rows["google_ads:20010001"]
    assert float(unmapped[1]) == 0.0 and unmapped[2] == 0.0  # spend without revenue: ROAS is 0
    assert rows["search_console:example"][2] is None  # revenue without spend


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
            metrics=["google_spend"], dimensions=["date", "campaign_name"], time_range=OCTOBER, limit=2
        ),
        clickhouse_executor(agent_client),
        TODAY,
    )
    assert answer.truncated and len(answer.rows) == 2 and "LIMIT 2" in answer.sql
