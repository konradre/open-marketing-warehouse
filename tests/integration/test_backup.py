"""Backup and restore against the real server: a backup carries its own row counts, the drill proves it
restores without touching the live data, and a restore brings back what was lost."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from clickhouse_connect.driver.client import Client

from omw import backup
from omw.backup import core

pytestmark = pytest.mark.integration

RAW = "raw.t12_backup_probe"
VIEW = "marts.t12_backup_probe_view"
OPS = "ops.t12_backup_probe"
STAMP = f"{datetime.now(tz=UTC):%Y%m%dT%H%M%S}"
NAMES = (f"t12-first-{STAMP}.zip", f"t12-miscounted-{STAMP}.zip", f"t12-second-{STAMP}.zip")
"""Unique per run: a backup file cannot be overwritten, and only the server can reach its disk."""


def count(client: Client, table: str) -> int:
    return int(client.query(f"SELECT count() FROM {table}").result_rows[0][0])


@pytest.fixture(scope="module")
def probes(pipeline_client: Client, admin_client: Client) -> Iterator[None]:
    """A table, a view over it (with table aliases, as the marts views have) and an ops table."""
    cleanup = (f"DROP VIEW IF EXISTS {VIEW}", f"DROP TABLE IF EXISTS {RAW}", f"DROP TABLE IF EXISTS {OPS}")
    for statement in cleanup:
        pipeline_client.command(statement)
    pipeline_client.command(f"CREATE TABLE {RAW} (id UInt32, day Date) ENGINE = MergeTree ORDER BY id")
    pipeline_client.command(f"INSERT INTO {RAW} SELECT number, today() FROM numbers(1000)")
    pipeline_client.command(f"CREATE TABLE {OPS} (id UInt32) ENGINE = MergeTree ORDER BY id")
    pipeline_client.command(f"INSERT INTO {OPS} VALUES (1), (2)")
    pipeline_client.command(
        f"CREATE VIEW {VIEW} (id UInt32 COMMENT 'probe') DEFINER = CURRENT_USER SQL SECURITY DEFINER AS "
        f"SELECT a.id AS id FROM {RAW} AS a INNER JOIN {RAW} AS b ON b.id = a.id WHERE a.id < 10"
    )
    yield
    for statement in cleanup:
        pipeline_client.command(statement)
    listed = ", ".join(f"'{name}'" for name in NAMES)
    for table in ("ops.backup_manifest", "ops.backup_log"):
        pipeline_client.command(f"DELETE FROM {table} WHERE backup IN ({listed})")
    for database in backup.DATABASES:
        admin_client.command(f"DROP DATABASE IF EXISTS {core.SCRATCH_PREFIX}{database}")


def test_a_backup_carries_its_manifest_and_the_drill_restores_it(
    probes: None, pipeline_client: Client
) -> None:
    made = backup.create(pipeline_client, NAMES[0])
    assert made.name == NAMES[0] and made.rows >= 1002 and made.tables >= 3 and made.bytes > 0
    found = pipeline_client.query(
        "SELECT concat(database_name, '.', table_name), rows FROM ops.backup_manifest "
        f"WHERE backup = '{NAMES[0]}' AND table_name LIKE 't12_backup_probe%'"
    ).result_rows
    manifest = {str(name): int(rows) for name, rows in found}
    assert manifest == {RAW: 1000, VIEW: 0, OPS: 2}

    pipeline_client.command(
        f"INSERT INTO {RAW} SELECT number + 5000, today() FROM numbers(50)"
    )  # after the backup
    drill = backup.verify(pipeline_client, NAMES[0])
    assert drill.ok, drill.problems
    assert drill.tables == made.tables and drill.rows >= made.rows
    assert count(pipeline_client, RAW) == 1050  # the drill restored beside the live data, not over it
    scratch = pipeline_client.query(
        "SELECT count() FROM system.databases WHERE name LIKE 'omw_drill_%'"
    ).result_rows
    assert scratch[0][0] == 0
    assert backup.latest(pipeline_client) == NAMES[0]
    listed = {item.name: item for item in backup.listing(pipeline_client)}
    assert listed[NAMES[0]].verified == "verified" and listed[NAMES[0]].rows == made.rows
    assert listed[NAMES[0]].file == "unknown"  # the backups disk is the server's; this process cannot see it


def test_the_agent_cannot_see_backups_or_drill_databases(probes: None, agent_client: Client) -> None:
    for table in ("ops.backup_manifest", "ops.backup_log", "ops.sync_runs", "system.backups"):
        with pytest.raises(Exception, match="ACCESS_DENIED|Not enough privileges|UNKNOWN_TABLE"):
            agent_client.query(f"SELECT count() FROM {table}")
    with pytest.raises(Exception, match="ACCESS_DENIED|Not enough privileges"):
        agent_client.command("BACKUP TABLE marts.t12_backup_probe_view TO Disk('backups', 't12-agent.zip')")


def test_the_drill_fails_when_the_backup_does_not_hold_what_the_manifest_says(
    probes: None, pipeline_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    honest = core._count

    def one_more(client: Client, database: str, table: str) -> int:
        found = honest(client, database, table)
        return found + 1 if (database, table) == ("raw", "t12_backup_probe") else found

    monkeypatch.setattr(core, "_count", one_more)  # as if a row was lost between the count and the backup
    backup.create(pipeline_client, NAMES[1])
    monkeypatch.setattr(core, "_count", honest)
    drill = backup.verify(pipeline_client, NAMES[1])
    assert not drill.ok
    assert drill.problems == ["raw.t12_backup_probe: 1050 rows restored, the manifest says 1051"]
    assert {item.name: item.verified for item in backup.listing(pipeline_client)}[NAMES[1]] == "verify_failed"
    with pytest.raises(
        backup.BackupError, match="the restore drill of t12-miscounted-.* failed, nothing was"
    ):
        backup.restore(pipeline_client, NAMES[1], force=True)
    assert (
        count(pipeline_client, RAW) == 1050
    )  # a backup that fails its drill is never restored over live data
    missing = backup.verify(pipeline_client, "t12-never-made.zip")
    assert not missing.ok and len(missing.problems) == 1
    assert "DB::Exception: Backup Disk('backups', 't12-never-made.zip') not found" in missing.problems[0]


def test_a_restore_is_refused_over_data_and_with_force_brings_back_what_was_lost(
    probes: None, pipeline_client: Client, agent_client: Client
) -> None:
    backup.create(pipeline_client, NAMES[2])
    before = {
        (database, table): count(pipeline_client, f"`{database}`.`{table}`")
        for database, table, kind in core._objects(pipeline_client, backup.DATABASES)
        if kind == "table" and database != "ops"
    }
    with pytest.raises(backup.BackupError, match="Pass --force"):
        backup.restore(pipeline_client, NAMES[2])

    pipeline_client.command(f"DROP VIEW {VIEW}")  # the accident
    pipeline_client.command(f"TRUNCATE TABLE {RAW}")
    assert count(pipeline_client, RAW) == 0

    drill = backup.restore(pipeline_client, NAMES[2], force=True)
    assert drill.ok
    after = {
        (database, table): count(pipeline_client, f"`{database}`.`{table}`")
        for database, table, kind in core._objects(pipeline_client, backup.DATABASES)
        if kind == "table" and database != "ops"
    }
    assert after == before and after[("raw", "t12_backup_probe")] == 1050
    # The view came back with its aliases intact, still readable by the agent through its definer.
    assert count(agent_client, VIEW) == 10
    events = pipeline_client.query(
        f"SELECT event FROM ops.backup_log WHERE backup = '{NAMES[2]}' ORDER BY at"
    ).result_rows
    assert events[-1] == ("restored",)
    assert NAMES[2] in {item.name for item in backup.listing(pipeline_client)}
