"""The `agent` ClickHouse user is the boundary (DESIGN.md §1, §7). Each limit is proven here."""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request

import pytest
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from tests.expected import AGENT_PROFILE_RAISED, AGENT_PROFILE_SETTINGS, AGENT_QUOTA
from tests.integration.conftest import setting

pytestmark = pytest.mark.integration

ACCESS_DENIED = 497
READONLY = 164
SETTING_CONSTRAINT_VIOLATION = 452
TOO_MANY_ROWS = 158
TIMEOUT_EXCEEDED = 159


def _error_code(exc: DatabaseError) -> int:
    """clickhouse-connect wraps the server text, which starts with `Code: <n>.`."""
    text = str(exc)
    marker = "Code: "
    start = text.find(marker)
    assert start >= 0, text
    return int(text[start + len(marker) :].split(".", 1)[0])


def _expect_error(client: Client, sql: str, codes: set[int]) -> None:
    with pytest.raises(DatabaseError) as info:
        client.query(sql)
    assert _error_code(info.value) in codes, str(info.value)[:300]


# ---- what the agent can see ---------------------------------------------------------------------------


def test_agent_reads_marts(agent_client: Client, probe_tables: dict[str, str]) -> None:
    assert agent_client.query("SELECT currentUser()").result_rows[0][0] == "agent"
    assert agent_client.query(f"SELECT count() FROM {probe_tables['marts']}").result_rows[0][0] == 3


def test_agent_reads_ops_freshness_only(agent_client: Client, probe_tables: dict[str, str]) -> None:
    agent_client.query("SELECT * FROM ops.freshness LIMIT 1")
    _expect_error(agent_client, f"SELECT * FROM {probe_tables['ops']}", {ACCESS_DENIED})


def test_raw_is_invisible_to_the_agent(agent_client: Client, probe_tables: dict[str, str]) -> None:
    _expect_error(agent_client, f"SELECT * FROM {probe_tables['raw']}", {ACCESS_DENIED})
    visible = {row[0] for row in agent_client.query("SHOW DATABASES").result_rows}
    assert "raw" not in visible
    assert "marts" in visible
    tables = {row[0] for row in agent_client.query("SELECT database FROM system.tables").result_rows}
    assert "raw" not in tables


# ---- what the agent cannot do -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO marts.t1_probe VALUES (4)",
        "CREATE TABLE marts.t1_agent_made (id UInt32) ENGINE = MergeTree ORDER BY id",
        "ALTER TABLE marts.t1_probe DELETE WHERE id = 1",
        "DROP TABLE marts.t1_probe",
        "TRUNCATE TABLE marts.t1_probe",
        "CREATE DATABASE t1_agent_made",
    ],
)
def test_agent_cannot_write(agent_client: Client, probe_tables: dict[str, str], sql: str) -> None:
    _expect_error(agent_client, sql, {READONLY, ACCESS_DENIED})
    assert agent_client.query(f"SELECT count() FROM {probe_tables['marts']}").result_rows[0][0] == 3


@pytest.mark.parametrize(("name", "value"), sorted(AGENT_PROFILE_SETTINGS.items()))
def test_profile_value_is_applied_and_const(agent_client: Client, name: str, value: str) -> None:
    rows = agent_client.query(
        "SELECT value, readonly FROM system.settings WHERE name = {name:String}", parameters={"name": name}
    ).result_rows
    assert rows, name
    applied, is_readonly = rows[0]
    assert applied == value, (name, applied)
    assert is_readonly == 1, name


def _quote(value: str) -> str:
    return value if value.lstrip("-").isdigit() else f"'{value}'"


@pytest.mark.parametrize(("name", "raised"), sorted(AGENT_PROFILE_RAISED.items()))
def test_set_cannot_raise_a_cap(agent_client: Client, name: str, raised: str) -> None:
    _expect_error(agent_client, f"SET {name} = {_quote(raised)}", {READONLY, SETTING_CONSTRAINT_VIOLATION})


@pytest.mark.parametrize(("name", "raised"), sorted(AGENT_PROFILE_RAISED.items()))
def test_query_level_settings_clause_cannot_raise_a_cap(agent_client: Client, name: str, raised: str) -> None:
    _expect_error(
        agent_client, f"SELECT 1 SETTINGS {name} = {_quote(raised)}", {READONLY, SETTING_CONSTRAINT_VIOLATION}
    )


@pytest.mark.parametrize(("name", "raised"), sorted(AGENT_PROFILE_RAISED.items()))
def test_client_sent_setting_cannot_raise_a_cap(agent_client: Client, name: str, raised: str) -> None:
    """A setting sent as an HTTP parameter, the way drivers send per-query settings."""
    params = {"user": "agent", "password": setting("OMW_AGENT_PASSWORD"), name: raised}
    url = (
        f"http://{setting('OMW_CLICKHOUSE_HOST')}:{setting('OMW_CLICKHOUSE_HTTP_PORT')}/?"
        + urllib.parse.urlencode(params)
    )
    request = urllib.request.Request(url, data=b"SELECT 1", method="POST")
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(request, timeout=10)
    body = info.value.read().decode()
    assert f"Code: {READONLY}." in body or f"Code: {SETTING_CONSTRAINT_VIOLATION}." in body, body[:300]


def test_readonly_blocks_settings_outside_the_profile_too(agent_client: Client) -> None:
    _expect_error(agent_client, "SET max_threads = 1", {READONLY})


# ---- the limits hold ----------------------------------------------------------------------------------


def test_big_result_breaks_at_max_result_rows(agent_client: Client) -> None:
    requested = 5_000_000
    max_rows = int(AGENT_PROFILE_SETTINGS["max_result_rows"])
    block, threads = agent_client.query(
        "SELECT toUInt64(getSetting('max_block_size')), toUInt64(getSetting('max_threads'))"
    ).result_rows[0]
    result = agent_client.query(f"SELECT number FROM numbers({requested})")
    returned = len(result.result_rows)
    # 'break' stops at block granularity, so the cap is approximate: at most one extra block per thread.
    assert max_rows <= returned <= max_rows + block * threads, returned
    assert returned < requested


def test_scan_breaks_at_max_rows_to_read(agent_client: Client) -> None:
    too_many = int(AGENT_PROFILE_SETTINGS["max_rows_to_read"]) * 2
    _expect_error(agent_client, f"SELECT count() FROM numbers({too_many})", {TOO_MANY_ROWS})


def test_wall_clock_timeout_holds(agent_client: Client) -> None:
    """A query that reads little but computes for minutes: only a wall-clock limit catches it
    (timeout_before_checking_execution_speed = 0), and it must stop at max_execution_time."""
    limit = int(AGENT_PROFILE_SETTINGS["max_execution_time"])
    heavy = (
        "SELECT count() FROM numbers(1000000) AS a, numbers(1000000) AS b WHERE a.number + b.number > 1999999"
    )
    started = time.monotonic()
    _expect_error(agent_client, heavy, {TIMEOUT_EXCEEDED})
    assert time.monotonic() - started < limit + 15


def test_quota_is_assigned_to_the_agent(admin_client: Client) -> None:
    rows = admin_client.query(
        "SELECT apply_to_list, keys FROM system.quotas WHERE name = 'agent_quota'"
    ).result_rows
    assert rows and "agent" in rows[0][0], rows
    assert rows[0][1] == ["user_name"]
    limits = admin_client.query(
        "SELECT duration, max_queries, max_read_bytes FROM system.quota_limits "
        "WHERE quota_name = 'agent_quota'"
    ).result_rows
    assert limits == [(AGENT_QUOTA["duration_seconds"], AGENT_QUOTA["queries"], AGENT_QUOTA["read_bytes"])]


def test_agent_queries_count_against_the_quota(agent_client: Client) -> None:
    usage = "SELECT queries, read_rows FROM system.quota_usage WHERE quota_name = 'agent_quota'"
    before = agent_client.query(usage).result_rows
    agent_client.query("SELECT count() FROM numbers(1000)")
    after = agent_client.query(usage).result_rows
    assert before and after
    assert after[0][0] >= before[0][0] + 1
    assert after[0][1] >= before[0][1] + 1000


# ---- the pipeline user --------------------------------------------------------------------------------


def test_pipeline_writes_raw_ops_and_marts(pipeline_client: Client, probe_tables: dict[str, str]) -> None:
    assert pipeline_client.query("SELECT currentUser()").result_rows[0][0] == "pipeline"
    try:
        pipeline_client.command("CREATE TABLE raw.t1_pipeline (id UInt32) ENGINE = MergeTree ORDER BY id")
        pipeline_client.command("INSERT INTO raw.t1_pipeline VALUES (1)")
        pipeline_client.command("CREATE TABLE ops.t1_pipeline (id UInt32) ENGINE = MergeTree ORDER BY id")
        pipeline_client.command("CREATE VIEW marts.t1_pipeline AS SELECT id FROM raw.t1_pipeline")
        assert pipeline_client.query("SELECT count() FROM marts.t1_pipeline").result_rows[0][0] == 1
    finally:
        pipeline_client.command("DROP VIEW IF EXISTS marts.t1_pipeline")
        pipeline_client.command("DROP TABLE IF EXISTS ops.t1_pipeline")
        pipeline_client.command("DROP TABLE IF EXISTS raw.t1_pipeline")
