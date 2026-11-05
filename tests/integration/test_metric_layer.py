"""Compiled metric queries run as the agent over the fixture loads of Meta, Google Ads, LinkedIn Ads, Stripe
and Twenty, with the post-sync verdicts folded into the caveats."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import dlt
import pytest
import responses
from clickhouse_connect.driver.client import Client

from omw.checks.runner import apply_ops_sql
from omw.metrics import Catalog, MetricRequest, Mood, TimeRange, load_catalog
from omw.metrics.runner import answer_request, clickhouse_executor
from omw.pacing import MetaPacer
from omw.pipelines import google_ads as google_pipeline
from omw.pipelines import linkedin_ads as linkedin_pipeline
from omw.pipelines import meta_ads as meta_pipeline
from omw.pipelines import stripe as stripe_pipeline
from omw.pipelines import twenty as twenty_pipeline
from omw.pipelines.marts import SQL_DIR, statements
from omw.sources.linkedin_ads import LinkedInAuth
from omw.sources.twenty.source import default_session as twenty_session
from tests.integration.conftest import setting
from tests.unit.google_ads.conftest import CUSTOMER, FakeGoogleAdsClient
from tests.unit.linkedin_ads import conftest as linkedin_fixtures
from tests.unit.meta_ads.conftest import ACCOUNT, FakeAccount
from tests.unit.stripe.conftest import FakeStripeClient
from tests.unit.twenty import conftest as twenty_fixtures

pytestmark = pytest.mark.integration

TODAY = date(2026, 10, 5)
OCTOBER = TimeRange(start=date(2026, 10, 1), end=date(2026, 10, 3))
DATASETS = ("meta_ads", "google_ads", "stripe", "linkedin_ads", "twenty")


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


def _pipeline(name: str, dataset: str, tmp_path_factory: pytest.TempPathFactory) -> dlt.Pipeline:
    return dlt.pipeline(
        name,
        destination=_destination(),
        dataset_name=dataset,
        pipelines_dir=str(tmp_path_factory.mktemp(name)),
    )


def _load_http_sources(tmp_path_factory: pytest.TempPathFactory) -> None:
    """LinkedIn and Twenty are served by `responses`; ClickHouse traffic passes through untouched."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add_passthru("http://127.0.0.1")
        mock.add_passthru("http://localhost")
        linkedin_fixtures.register_api(mock)
        twenty_fixtures.register_api(mock)
        linkedin_pipeline.run_daily(
            _pipeline("linkedin_ml", "linkedin_ads", tmp_path_factory),
            today=TODAY,
            account_ids=[linkedin_fixtures.ACCOUNT],
            auth=LinkedInAuth(linkedin_fixtures.VERSION, token_provider=lambda: "t"),
        )
        twenty_pipeline.run_daily(
            _pipeline("twenty_ml", "twenty", tmp_path_factory),
            base_url=twenty_fixtures.BASE_URL,
            api_key="k",
            session=twenty_session(2, 0.0),
        )


@pytest.fixture(scope="module")
def warehouse(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Catalog]:
    for dataset in DATASETS:
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
    _load_http_sources(tmp_path_factory)
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
    for dataset in DATASETS:
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
