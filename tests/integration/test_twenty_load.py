"""Twenty fixtures loaded through dlt into ClickHouse as `pipeline`, read back as `agent`."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import dlt
import pytest
import responses
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from omw.pipelines.twenty import run_daily
from omw.sources.twenty.source import default_session
from tests.integration.conftest import setting
from tests.unit.twenty.conftest import BASE_URL, register_api

pytestmark = pytest.mark.integration

DATASET = "twenty"


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


def _run(pipeline: dlt.Pipeline, opportunities: str = "opportunities") -> dict[str, int]:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add_passthru("http://127.0.0.1")
        mock.add_passthru("http://localhost")
        register_api(mock, opportunities)
        return run_daily(pipeline, base_url=BASE_URL, api_key="k", session=default_session(2, 0.0))


@pytest.fixture(scope="module")
def loaded(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dlt.Pipeline]:
    _drop(admin_client)
    pipeline = dlt.pipeline(
        pipeline_name="twenty_it",
        destination=_destination(),
        dataset_name=DATASET,
        pipelines_dir=str(tmp_path_factory.mktemp("dlt")),
    )
    counts = _run(pipeline)
    assert counts["opportunities"] == 3
    yield pipeline
    _drop(admin_client)


def test_pipeline_views_resolve_attribution_and_amounts(loaded: dlt.Pipeline, agent_client: Client) -> None:
    for view in ("twenty_pipeline", "twenty_pipeline_by_stage"):
        columns = agent_client.query(
            "SELECT name, comment FROM system.columns WHERE database = 'marts' AND table = {t:String}",
            parameters={"t": view},
        ).result_rows
        assert columns and all(comment for _, comment in columns), view
    rows = {
        r[0]: r[1:]
        for r in agent_client.query(
            "SELECT opportunity_name, stage, amount, currency, company_name, person_email, "
            "campaign_key, utm_source, attributed "
            "FROM marts.twenty_pipeline"
        ).result_rows
    }
    acme = rows["Acme - 120 seats"]
    assert acme[0] == "PROPOSAL" and Decimal(str(acme[1])) == Decimal("36000") and acme[2] == "EUR"
    assert acme[3] == "Acme Widgets" and acme[4] == "ada@acme.example"
    assert (
        acme[5] == "meta:23850000000000001" and acme[6] == "facebook" and bool(acme[7]) is True
    )  # from the company
    globex = rows["Globex - pilot"]
    assert globex[5] == "search_console:example" and globex[6] == "google"  # from the point of contact
    initech = rows["Initech - enterprise"]
    assert (
        initech[5] == "events:webinar_oct" and initech[3] == "Initech" and initech[4] == ""
    )  # from the opportunity itself
    by_stage = {
        (r[0], r[1]): r[2:]
        for r in agent_client.query(
            "SELECT stage, currency, opportunities, amount FROM marts.twenty_pipeline_by_stage"
        ).result_rows
    }
    assert by_stage[("PROPOSAL", "EUR")][0] == 1 and Decimal(str(by_stage[("NEW", "USD")][1])) == Decimal(
        "120000"
    )


def test_a_moved_opportunity_is_merged_not_duplicated(
    loaded: dlt.Pipeline, admin_client: Client, agent_client: Client
) -> None:
    counts = _run(loaded, opportunities="opportunities_updated")
    assert counts.get("opportunities", 0) == 1
    assert admin_client.query("SELECT count() FROM raw.twenty___opportunities").result_rows[0][0] == 3
    stage = agent_client.query(
        "SELECT stage, close_date FROM marts.twenty_pipeline WHERE opportunity_name = 'Acme - 120 seats'"
    ).result_rows[0]
    assert stage[0] == "CUSTOMER" and stage[1].year == 2026 and stage[1].month == 10
    with pytest.raises(DatabaseError, match="Code: 497"):
        agent_client.query(f"SELECT count() FROM raw.{DATASET}___companies")
