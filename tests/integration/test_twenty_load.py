"""Twenty fixtures loaded through dlt into ClickHouse as `pipeline`, read back as `agent`."""

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

from omw.metrics import MetricRequest, Mood, TimeRange, load_catalog
from omw.metrics.runner import answer_request, clickhouse_executor
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


def _run(
    pipeline: dlt.Pipeline,
    opportunities: str = "opportunities",
    deleted: str | None = None,
    won: list[str] | None = None,
    timeline: str = "timeline_activities",
    lost: list[str] | None = None,
) -> dict[str, int]:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add_passthru("http://127.0.0.1")
        mock.add_passthru("http://localhost")
        register_api(mock, opportunities, deleted, timeline)
        return run_daily(
            pipeline, won=won, lost=lost, base_url=BASE_URL, api_key="k", session=default_session(2, 0.0)
        )


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
    assert counts["opportunities"] == 3 and counts["notes"] == 2 and counts["task_targets"] == 2
    assert counts["timeline_activities"] == 7
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


def _won(agent_client: Client, **request: Any) -> tuple[list[tuple[Any, ...]], Any]:
    answer = answer_request(
        load_catalog(),
        MetricRequest(time_range=TimeRange(start=date(2026, 9, 1), end=date(2026, 10, 31)), **request),
        clickhouse_executor(agent_client),
        date(2026, 11, 1),
    )
    assert answer.mood in (Mood.SERVE, Mood.DISCLOSE), answer.reason
    return [tuple(row) for row in answer.rows], answer


def test_the_won_metrics_follow_the_configured_closed_won_stages(
    loaded: dlt.Pipeline, agent_client: Client
) -> None:
    """The fixture pipeline has opportunities in PROPOSAL (36 000 EUR), MEETING (9 000 EUR) and NEW
    (120 000 USD), none in Twenty's default closed-won stage."""
    comment = (
        "SELECT comment FROM system.columns "
        "WHERE database = 'marts' AND table = 'twenty_pipeline' AND name = 'is_won'"
    )
    assert str(agent_client.command(comment)).endswith("Closed-won stages here: CUSTOMER")
    rows, _ = _won(agent_client, metrics=["won_opportunities", "opportunities"])
    assert [tuple(float(v) for v in row) for row in rows] == [(0.0, 3.0)]

    # A workspace that closes deals in two stages of its own names them, and the same metrics follow.
    _run(loaded, won=["PROPOSAL", "MEETING"])
    try:
        assert str(agent_client.command(comment)).endswith("Closed-won stages here: PROPOSAL, MEETING")
        rows, _ = _won(agent_client, metrics=["won_opportunities", "opportunities"])
        assert [tuple(float(v) for v in row) for row in rows] == [(2.0, 3.0)]
        rows, _ = _won(agent_client, metrics=["won_amount"], filters={"currency": "EUR"})
        assert float(rows[0][0]) == 45000.0
        rows, _ = _won(agent_client, metrics=["won_amount"], filters={"currency": "USD"})
        assert float(rows[0][0]) == 0.0
        by_flag, _ = _won(agent_client, metrics=["opportunities"], dimensions=["is_won"])
        assert {bool(flag): float(count) for flag, count in by_flag} == {True: 2.0, False: 1.0}
        won_stages_seen = agent_client.query(
            "SELECT groupArray(stage) FROM (SELECT DISTINCT stage FROM marts.twenty_pipeline WHERE is_won "
            "ORDER BY stage)"
        ).result_rows[0][0]
        assert won_stages_seen == ["MEETING", "PROPOSAL"]
    finally:
        _run(loaded)  # back to the default for the tests that follow
    assert str(agent_client.command(comment)).endswith("Closed-won stages here: CUSTOMER")


def test_closed_lost_stages_split_the_pipeline_into_won_lost_and_open(
    loaded: dlt.Pipeline, agent_client: Client
) -> None:
    """Twenty's default pipeline has no lost stage, so nothing is lost until the deployment names one. The
    fixture pipeline: PROPOSAL (36 000 EUR), MEETING (9 000 EUR) and NEW (120 000 USD)."""
    comment = (
        "SELECT comment FROM system.columns "
        "WHERE database = 'marts' AND table = 'twenty_pipeline' AND name = 'is_lost'"
    )
    assert str(agent_client.command(comment)).endswith("Closed-lost stages here: none")
    assert agent_client.command("SELECT countIf(is_lost) FROM marts.twenty_pipeline") == 0
    _run(loaded, won=["PROPOSAL"], lost=["NEW"])
    try:
        assert str(agent_client.command(comment)).endswith("Closed-lost stages here: NEW")
        rows, _ = _won(agent_client, metrics=["opportunities"], dimensions=["is_won", "is_lost"])
        split = {(bool(won), bool(lost)): float(count) for won, lost, count in rows}
        assert split == {(True, False): 1.0, (False, True): 1.0, (False, False): 1.0}  # won, lost, open
        rows, _ = _won(
            agent_client, metrics=["pipeline_amount"], dimensions=["is_lost"], filters={"currency": "USD"}
        )
        assert {bool(lost): float(amount) for lost, amount in rows} == {True: 120000.0}
    finally:
        _run(loaded)
    assert agent_client.command("SELECT countIf(is_lost) FROM marts.twenty_pipeline") == 0


def _by(rows: list[tuple[Any, ...]]) -> dict[Any, float]:
    return {(row[0] if len(row) == 2 else tuple(row[:-1])): float(row[-1]) for row in rows}


def test_the_timeline_dates_what_happened_to_each_record(loaded: dlt.Pipeline, agent_client: Client) -> None:
    columns = dict(
        agent_client.query(
            "SELECT name, comment FROM system.columns WHERE database = 'marts' AND table = 'twenty_timeline'"
        ).result_rows
    )
    assert columns and all(columns.values())
    # What a record held before and after an edit, and the names of linked records, never leave raw.
    assert not {"properties", "linked_record_name", "linked_record_id"} & set(columns)
    rows = agent_client.query(
        "SELECT toString(date), event, object, action, is_linked, changed_fields, stage_before, stage_after, "
        "became_won, opportunity_name, company_name, campaign_key, person_id != '', member_id != '' "
        "FROM marts.twenty_timeline ORDER BY happens_at"
    ).result_rows
    assert [(r[0], r[1]) for r in rows] == [
        ("2026-09-15", "opportunity.created"),
        ("2026-09-20", "opportunity.updated"),
        ("2026-09-26", "opportunity.updated"),
        ("2026-10-01", "opportunity.updated"),
        ("2026-10-01", "linked-note.created"),
        ("2026-10-02", "person.updated"),
        ("2026-10-02", "company.updated"),
    ]
    # Both shapes of the object land in the same columns: the second move comes in the newer one.
    assert [(r[6], r[7]) for r in rows if r[7]] == [
        ("NEW", "MEETING"),
        ("NEW", "MEETING"),
        ("MEETING", "PROPOSAL"),
    ]
    assert rows[2][5] == "amount,stage" and rows[2][9] == "Globex - pilot"
    acme = [r for r in rows if r[9] == "Acme - 120 seats"]
    assert len(acme) == 4 and all(r[10] == "Acme Widgets" and r[11] == "meta:23850000000000001" for r in acme)
    linked = rows[4]
    assert (linked[2], linked[3], bool(linked[4])) == ("note", "linked", True)
    person, company = rows[5], rows[6]
    assert bool(person[12]) and not bool(person[13]) and person[2] == "person" and person[9] == ""
    assert company[10] == "Acme Widgets" and company[5] == "employees"
    assert not any(bool(r[8]) for r in rows)  # nothing reached CUSTOMER, Twenty's default closed-won stage

    rows, _ = _won(agent_client, metrics=["crm_events", "crm_stage_changes", "crm_wins"])
    assert [tuple(float(v) for v in row) for row in rows] == [(7.0, 3.0, 0.0)]
    by_stage, _ = _won(agent_client, metrics=["crm_stage_changes"], dimensions=["stage_after"])
    assert {k: v for k, v in _by(by_stage).items() if v} == {"MEETING": 2.0, "PROPOSAL": 1.0}
    by_object, _ = _won(agent_client, metrics=["crm_events"], dimensions=["object"])
    assert _by(by_object) == {"opportunity": 4.0, "note": 1.0, "person": 1.0, "company": 1.0}
    by_key, _ = _won(agent_client, metrics=["crm_events"], dimensions=["campaign_key"])
    assert _by(by_key)["meta:23850000000000001"] == 5.0  # the four on the Acme opportunity, one on Acme
    # An event on an opportunity carries the key the pipeline gives that opportunity, so wins by campaign
    # and won opportunities by campaign split the same way. Globex's key comes from its point of contact.
    disagreeing = agent_client.query(
        "SELECT t.opportunity_name, t.campaign_key, p.campaign_key FROM marts.twenty_timeline AS t "
        "INNER JOIN marts.twenty_pipeline AS p ON p.opportunity_id = t.opportunity_id "
        "WHERE t.campaign_key != p.campaign_key"
    ).result_rows
    assert disagreeing == []
    assert _by(by_key)["search_console:example"] >= 1.0


def test_wins_are_dated_by_the_day_the_stage_was_entered(loaded: dlt.Pipeline, agent_client: Client) -> None:
    """``won_opportunities`` dates a win by the day the opportunity was created. The change feed has the
    day it moved, and follows the same configured closed-won stages."""
    _run(loaded, won=["PROPOSAL"])
    try:
        by_day, _ = _won(agent_client, metrics=["crm_wins"], dimensions=["date"])
        assert {str(day): wins for day, wins in _by(by_day).items() if wins} == {"2026-10-01": 1.0}
        created, _ = _won(agent_client, metrics=["won_opportunities"], dimensions=["date"])
        assert {str(day): wins for day, wins in _by(created).items() if wins} == {"2026-09-15": 1.0}
        _run(loaded, won=["PROPOSAL", "MEETING"])
        by_day, _ = _won(agent_client, metrics=["crm_wins"], dimensions=["date"])
        # Two opportunities entered MEETING. Acme moving on to PROPOSAL was already won: not a second win.
        assert {str(day): wins for day, wins in _by(by_day).items() if wins} == {
            "2026-09-20": 1.0,
            "2026-09-26": 1.0,
        }
        comment = agent_client.command(
            "SELECT comment FROM system.columns "
            "WHERE database = 'marts' AND table = 'twenty_timeline' AND name = 'became_won'"
        )
        assert str(comment).endswith("Closed-won stages here: PROPOSAL, MEETING")
    finally:
        _run(loaded)


def test_activity_attaches_notes_and_tasks_to_their_records(
    loaded: dlt.Pipeline, agent_client: Client
) -> None:
    rows = agent_client.query(
        "SELECT kind, title, status, opportunity_name, company_name, person_id != '', campaign_key, date "
        "FROM marts.twenty_activity ORDER BY created_at, opportunity_name, company_name"
    ).result_rows
    assert len(rows) == 5  # two notes (one on two records), two tasks, one row per attached record
    kickoff = [r for r in rows if r[1] == "Kick-off call"]
    assert {(r[3], r[4]) for r in kickoff} == {("Acme - 120 seats", "Acme Widgets"), ("", "Acme Widgets")}
    assert all(r[6] == "meta:23850000000000001" for r in kickoff)  # from the opportunity's company
    pricing = next(r for r in rows if r[1] == "Pricing questions")
    assert bool(pricing[5]) and pricing[6] == "search_console:example"  # the attached person's key
    demo = next(r for r in rows if r[1] == "Schedule demo")
    assert demo[0] == "task" and demo[2] == "TODO" and demo[3] == "Initech - enterprise"
    assert demo[6] == "events:webinar_oct" and demo[7].isoformat() == "2026-10-03"
    columns = agent_client.query(
        "SELECT name, comment FROM system.columns WHERE database = 'marts' AND table = 'twenty_activity'"
    ).result_rows
    assert columns and all(comment for _, comment in columns)


def test_a_moved_opportunity_is_merged_not_duplicated(
    loaded: dlt.Pipeline, admin_client: Client, agent_client: Client
) -> None:
    counts = _run(loaded, opportunities="opportunities_updated", timeline="timeline_activities_won")
    assert counts.get("opportunities", 0) == 1 and counts.get("timeline_activities", 0) == 1
    assert admin_client.query("SELECT count() FROM raw.twenty___opportunities").result_rows[0][0] == 3
    # The move into CUSTOMER is one more event in the feed, and a win on the day it happened.
    assert admin_client.query("SELECT count() FROM raw.twenty___timeline_activities").result_rows[0][0] == 8
    by_day, _ = _won(agent_client, metrics=["crm_wins"], dimensions=["date"])
    assert {str(row[0]): float(row[1]) for row in by_day if float(row[1])} == {"2026-10-04": 1.0}
    stage = agent_client.query(
        "SELECT stage, close_date FROM marts.twenty_pipeline WHERE opportunity_name = 'Acme - 120 seats'"
    ).result_rows[0]
    assert stage[0] == "CUSTOMER" and stage[1].year == 2026 and stage[1].month == 10
    with pytest.raises(DatabaseError, match="Code: 497"):
        agent_client.query(f"SELECT count() FROM raw.{DATASET}___companies")


def test_a_deleted_opportunity_leaves_the_pipeline_but_stays_in_raw(
    loaded: dlt.Pipeline, admin_client: Client, agent_client: Client
) -> None:
    counts = _run(loaded, opportunities="empty", deleted="opportunities_deleted")
    assert counts.get("opportunities", 0) == 1  # the deleted pass merged one row into the same table
    assert admin_client.query("SELECT count() FROM raw.twenty___opportunities").result_rows[0][0] == 3
    deleted_at = admin_client.query(
        "SELECT deleted_at FROM raw.twenty___opportunities WHERE name = 'Globex - pilot'"
    ).result_rows[0][0]
    assert deleted_at.year == 2026 and deleted_at.month == 10 and deleted_at.day == 4
    names = {
        r[0] for r in agent_client.query("SELECT opportunity_name FROM marts.twenty_pipeline").result_rows
    }
    assert "Globex - pilot" not in names and len(names) == 2
