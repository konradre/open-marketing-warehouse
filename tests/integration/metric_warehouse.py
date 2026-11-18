"""The warehouse the metric layer's integration tests run on: the fixture loads of Meta, Google Ads, LinkedIn
Ads, Stripe and Twenty through their real pipelines, the campaign keys the pipelines derive and two set by
hand. Each test module wraps ``loaded`` in its own module-scoped fixture."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import dlt
import pytest
import responses
from clickhouse_connect.driver.client import Client

from omw.metrics import Catalog, TimeRange, load_catalog
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
from tests.unit.meta_ads.conftest import ACCOUNT, FakeAccount, fake_lead_fetcher
from tests.unit.stripe.conftest import FakeStripeClient
from tests.unit.twenty import conftest as twenty_fixtures

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
            leads=True,
        )
        twenty_pipeline.run_daily(
            _pipeline("twenty_ml", "twenty", tmp_path_factory),
            base_url=twenty_fixtures.BASE_URL,
            api_key="k",
            session=twenty_session(2, 0.0),
        )


@contextmanager
def loaded(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Catalog]:
    """Load every fixture source, yield the catalog, then drop what was loaded."""
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
        leads=True,
        lead_fetcher=fake_lead_fetcher,
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
    # The pipelines derived a key per campaign (the slug of its name). Map one Meta and one Google campaign by
    # hand onto the keys the Stripe fixtures carry in their metadata; manual rows outrank derived ones.
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
