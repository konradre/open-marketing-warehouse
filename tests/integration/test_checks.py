"""The checks against real loads: an injected over-count, a stale source, ops.freshness read as the agent."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import dlt
import pytest
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from omw.checks import Status, run_checks
from omw.checks.freshness import token_events_sql
from omw.checks.results import CheckResult, write_results
from omw.checks.runner import apply_ops_sql
from omw.pipelines import ga4 as ga4_pipeline
from omw.pipelines import search_console as sc_pipeline
from omw.pipelines.instants import utc_wall_clock
from omw.pipelines.marts import SQL_DIR, statements
from omw.pipelines.ops_log import PacingEvent, record_pacing_events
from tests.integration.conftest import connect, setting
from tests.unit.ga4.conftest import PROPERTY, FakeGA4Client
from tests.unit.search_console.conftest import SITE, FakeSearchConsoleClient

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
def loaded(admin_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Client]:
    for dataset in ("ga4", "search_console"):
        _drop(admin_client, dataset)
    admin_client.command("DROP TABLE IF EXISTS ops.check_results")
    # DROP TABLE removes a view too; the T1 boundary fixture may have left a placeholder table here.
    admin_client.command("DROP TABLE IF EXISTS ops.freshness")
    admin_client.command("DROP TABLE IF EXISTS ops.check_verdicts")
    ga4_pipeline.run_daily(
        dlt.pipeline(
            "ga4_ck",
            destination=_destination(),
            dataset_name="ga4",
            pipelines_dir=str(tmp_path_factory.mktemp("g")),
        ),
        today=TODAY,
        property_ids=[PROPERTY],
        client=FakeGA4Client(),
    )
    sc_pipeline.run_daily(
        dlt.pipeline(
            "sc_ck",
            destination=_destination(),
            dataset_name="search_console",
            pipelines_dir=str(tmp_path_factory.mktemp("s")),
        ),
        today=TODAY,
        site_urls=[SITE],
        client=FakeSearchConsoleClient(),
    )
    pipeline_client = connect("pipeline", setting("OMW_PIPELINE_PASSWORD"))
    apply_ops_sql(pipeline_client)
    yield pipeline_client
    pipeline_client.close()
    for dataset in ("ga4", "search_console"):
        _drop(admin_client, dataset)


def test_checks_pass_on_clean_loads_and_land_in_ops(loaded: Client, admin_client: Client) -> None:
    # The sync log carries real load times, so freshness is judged against the real clock.
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    run = run_checks(loaded, now, {"ga4": 30.0, "search_console": 48.0}, today=TODAY)
    by_key = {(r.source, r.check, r.report): r for r in run.results}
    assert by_key[("ga4", "overcount", "channels")].status is Status.PASS
    assert by_key[("search_console", "overcount", "queries")].status is Status.PASS
    assert by_key[("ga4", "rowcount", "core")].status is Status.PASS
    assert by_key[("ga4", "freshness", "sync")].status is Status.PASS
    assert by_key[("search_console", "freshness", "sync")].status is Status.PASS
    stored = admin_client.query("SELECT count() FROM ops.check_results").result_rows[0][0]
    assert stored == len(run.results)


def test_an_injected_overcount_fails_and_the_cli_would_exit_nonzero(
    loaded: Client, admin_client: Client
) -> None:
    admin_client.command(
        "INSERT INTO raw.ga4___channels (source, report, property_id, date, channel_group, is_other, "
        "sessions, total_users, "
        "new_users, engaged_sessions, page_views, key_events, total_revenue, _dlt_load_id, _dlt_id) "
        f"VALUES ('ga4', 'channels', '{PROPERTY}', '2026-10-02', 'Injected', 0, 500, 1, 1, 1, 1, 0, 0, "
        "'x', 'injected')"
    )
    try:
        run = run_checks(loaded, datetime(2026, 10, 5, 6), {"ga4": 30.0}, sources={"ga4"})
        channels = next(r for r in run.results if r.check == "overcount" and r.report == "channels")
        assert channels.status is Status.FAIL and channels.observed > 1.01 and not run.ok
        landing = next(r for r in run.results if r.check == "overcount" and r.report == "landing_pages")
        assert landing.status is Status.PASS
    finally:
        admin_client.command("DELETE FROM raw.ga4___channels WHERE _dlt_id = 'injected'")


def test_stale_source_fails_freshness_and_the_agent_reads_the_verdict(
    loaded: Client, agent_client: Client
) -> None:
    later = datetime.now(tz=UTC).replace(tzinfo=None) + timedelta(days=5)
    run = run_checks(loaded, later, {"ga4": 30.0, "search_console": 48.0, "stripe": 30.0}, today=TODAY)
    verdicts = {r.source: r for r in run.results if r.check == "freshness"}
    assert verdicts["ga4"].status is Status.FAIL and "budget 30h" in verdicts["ga4"].detail
    assert verdicts["stripe"].status is Status.FAIL and "never synced" in verdicts["stripe"].detail
    rows = {
        r[0]: r
        for r in agent_client.query(
            "SELECT source, status, age_hours, budget_hours, detail FROM ops.freshness"
        ).result_rows
    }
    assert rows["ga4"][1] == "fail" and rows["ga4"][3] == 30.0 and rows["stripe"][1] == "fail"
    columns = agent_client.query(
        "SELECT name, comment FROM system.columns WHERE database = 'ops' AND table = 'freshness'"
    ).result_rows
    assert columns and all(comment for _, comment in columns)
    latest = {
        (str(r[0]), str(r[1]), str(r[2])): str(r[3])
        for r in agent_client.query(
            "SELECT source, check, report, status FROM ops.check_verdicts"
        ).result_rows
    }
    assert (
        latest[("ga4", "freshness", "sync")] == "fail" and latest[("stripe", "freshness", "sync")] == "fail"
    )
    assert latest[("ga4", "overcount", "channels")] == "pass"
    with pytest.raises(DatabaseError, match="Code: 497"):
        agent_client.query("SELECT count() FROM ops.check_results")


def test_rowcount_flags_an_empty_current_window(loaded: Client) -> None:
    # today = 2026-10-12: the current window (10-05..10-11) is empty, the one before holds the fixture days.
    run = run_checks(loaded, datetime(2026, 10, 12, 6), sources={"ga4"}, write=False)
    rowcount = next(r for r in run.results if r.check == "rowcount")
    assert rowcount.status is Status.FAIL and rowcount.observed == 0.0 and rowcount.expected == 3.0


def test_check_and_pacing_times_are_stored_as_the_instant_they_were_written(
    loaded: Client, admin_client: Client
) -> None:
    """Both columns were written as UTC wall-clock without a zone, which the driver reads in the host's
    zone: on a host that is not on UTC they landed shifted by its offset. A token that expired an hour ago
    then fell outside (or stayed inside) the freshness check's 24-hour window by that many hours."""
    write_results(loaded, [CheckResult("instants", "freshness", "sync", Status.PASS, 1, 2, "ok")])
    for statement in statements((SQL_DIR / "20_meta_ads_ops.sql").read_text()):
        loaded.command(statement)
    record_pacing_events(loaded, [PacingEvent("instants", "1", "daily", "token_expired", "token")])
    try:
        latest = (
            "SELECT toUnixTimestamp(max({column})), toUnixTimestamp(now()) FROM {table} "
            "WHERE source = 'instants'"
        )
        checked, server_now = admin_client.query(
            latest.format(column="checked_at", table="ops.check_results")
        ).result_rows[0]
        recorded, _ = admin_client.query(
            latest.format(column="recorded_at", table="ops.pacing_events")
        ).result_rows[0]
        assert abs(checked - server_now) < 120 and abs(recorded - server_now) < 120
        # The freshness check's own window query finds the event just written, and not through a window
        # that only opens an hour from now.
        seen = loaded.query(token_events_sql(utc_wall_clock() - timedelta(hours=1))).result_rows
        assert [row[0] for row in seen if row[0] == "instants"] == ["instants"]
        later = loaded.query(token_events_sql(utc_wall_clock() + timedelta(hours=1))).result_rows
        assert not [row for row in later if row[0] == "instants"]
    finally:
        admin_client.command("DELETE FROM ops.pacing_events WHERE source = 'instants'")
        admin_client.command("DELETE FROM ops.check_results WHERE source = 'instants'")
