"""Backup names, pruning, the statements a backup and a drill send, and what a restore forgets locally."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from omw import backup
from omw.backup import cli as backup_cli
from omw.backup.core import brief, checked
from tests.unit.checks.conftest import FakeWarehouse, Result


def test_scheduled_names_sort_by_time_and_only_plain_names_are_accepted() -> None:
    name = backup.new_name(datetime(2026, 11, 18, 6, 0, 5, tzinfo=UTC))
    assert name == "omw-20261118T060005Z.zip" and checked(name) == name
    assert checked("before-upgrade_2.zip") == "before-upgrade_2.zip"
    for bad in (
        "x.zip'); DROP DATABASE raw; --",
        "../etc/passwd.zip",
        "backup",
        "a b.zip",
        ".hidden.zip",
        "",
    ):
        with pytest.raises(backup.BackupError, match="is not a backup name"):
            checked(bad)


def test_prune_keeps_the_newest_scheduled_backups_and_nothing_else_goes(tmp_path: Path) -> None:
    scheduled = [f"omw-202611{day:02d}T060000Z.zip" for day in range(1, 10)]
    others = ["before-upgrade.zip", "omw-notes.txt", "omw-20261101T060000Z.zip.part"]
    for name in [*scheduled, *others]:
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "omw-20261199T060000Z.zip").mkdir()  # a directory with a backup's name is not a backup
    warehouse = FakeWarehouse()
    removed = backup.prune(tmp_path, 7, warehouse)
    assert removed == scheduled[:2]
    left = {path.name for path in tmp_path.iterdir()}
    assert left == {*scheduled[2:], *others, "omw-20261199T060000Z.zip"}
    assert [row[0][1:3] for _, row, _ in warehouse.inserted] == [[name, "pruned"] for name in removed]
    assert backup.prune(tmp_path, 7) == []
    with pytest.raises(backup.BackupError, match="keep must be at least 1"):
        backup.prune(tmp_path, 0)
    assert len(list(tmp_path.glob("omw-2026*Z.zip"))) == 8  # a refused prune deleted nothing


class BackupWarehouse(FakeWarehouse):
    """Three objects in the warehouse; answers counts, the BACKUP statement and its size."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_backup = False

    def query(self, sql: str) -> Result:
        self.queries.append(sql)
        if "FROM system.tables" in sql:
            return Result(
                [
                    ["marts", "ga4_core", "View"],
                    ["ops", "check_results", "MergeTree"],
                    ["raw", "ga4___core", "MergeTree"],
                ]
            )
        if sql.startswith("SELECT count() FROM"):
            return Result([[60 if "ga4___core" in sql else 12]])
        if sql.startswith("BACKUP"):
            if self.fail_backup:
                raise RuntimeError(
                    '{"exception": "Code: 243. DB::Exception: Cannot reserve 1.00 MiB, not enough space"}'
                )
            return Result([["0d5a1f3e", "BACKUP_CREATED"]])
        if "FROM system.backups" in sql:
            return Result([[830735]])
        raise AssertionError(sql)


def test_create_writes_the_manifest_before_the_backup_statement() -> None:
    warehouse = BackupWarehouse()
    made = backup.create(warehouse, now=datetime(2026, 11, 18, 6, 0, tzinfo=UTC))
    assert made == backup.Backup("omw-20261118T060000Z.zip", 3, 72, 830735)
    manifest, log = warehouse.inserted
    assert manifest[0] == "ops.backup_manifest"
    assert [row[2:] for row in manifest[1]] == [
        ["marts", "ga4_core", "view", 0],
        ["ops", "check_results", "table", 12],
        ["raw", "ga4___core", "table", 60],
    ]
    statement = next(sql for sql in warehouse.queries if sql.startswith("BACKUP"))
    assert statement == (
        "BACKUP DATABASE raw, DATABASE marts, DATABASE ops TO Disk('backups', 'omw-20261118T060000Z.zip')"
    )
    counted = [i for i, sql in enumerate(warehouse.queries) if sql.startswith("SELECT count()")]
    assert max(counted) < warehouse.queries.index(statement)  # the counts are what the backup must restore to
    assert log[0] == "ops.backup_log" and log[1][0][1:6] == [
        "omw-20261118T060000Z.zip",
        "created",
        3,
        72,
        830735,
    ]
    assert any("CREATE TABLE IF NOT EXISTS ops.backup_manifest" in command for command in warehouse.commands)


def test_a_failed_backup_is_logged_and_raised_in_one_line() -> None:
    warehouse = BackupWarehouse()
    warehouse.fail_backup = True
    with pytest.raises(backup.BackupError, match="Code: 243. DB::Exception: Cannot reserve 1.00 MiB"):
        backup.create(warehouse, "manual.zip")
    assert warehouse.inserted[-1][1][0][1:3] == ["manual.zip", "failed"]
    assert brief(ValueError("first line\nsecond line")) == "ValueError: first line"
    assert brief(RuntimeError("")) == "RuntimeError: RuntimeError"


def test_a_restore_refuses_a_warehouse_that_holds_anything_unless_forced() -> None:
    warehouse = BackupWarehouse()
    with pytest.raises(backup.BackupError, match="hold 3 tables and views .* Pass --force"):
        backup.restore(warehouse, "omw-20261118T060000Z.zip")
    assert not any(sql.startswith("RESTORE") for sql in warehouse.queries) and warehouse.commands == []
    with pytest.raises(backup.BackupError, match="is not a backup name"):
        backup.restore(warehouse, "nope", force=True)


def test_a_restore_onto_a_stack_that_only_holds_the_backup_bookkeeping_is_not_refused() -> None:
    """A first attempt on an empty stack (the file not readable yet) leaves ops.backup_log behind. The
    second attempt must not ask for --force because of it."""

    class Empty(BackupWarehouse):
        def query(self, sql: str) -> Result:
            if "FROM system.tables" in sql and "omw_drill_" not in sql:
                self.queries.append(sql)
                return Result([["ops", "backup_log", "MergeTree"], ["ops", "backup_manifest", "MergeTree"]])
            if sql.startswith("RESTORE"):
                self.queries.append(sql)
                raise RuntimeError(
                    "Code: 76. DB::ErrnoException: Cannot open file /backups/x.zip: Permission denied"
                )
            return super().query(sql)

    warehouse = Empty()
    with pytest.raises(backup.BackupError, match="the restore drill of x.zip failed, nothing was replaced"):
        backup.restore(warehouse, "x.zip")  # it got as far as the drill: not refused for holding data
    assert any(sql.startswith("RESTORE DATABASE raw AS omw_drill_raw") for sql in warehouse.queries)
    assert not any(command.startswith("DROP TABLE") for command in warehouse.commands)
    assert warehouse.inserted[-1][1][0][2] == "verify_failed"
    assert "DB::ErrnoException: Cannot open file /backups/x.zip" in warehouse.inserted[-1][1][0][6]


def test_there_must_be_a_backup_to_verify(warehouse: FakeWarehouse) -> None:
    warehouse.when("FROM ops.backup_log WHERE event = 'created'", [["", 0]])
    with pytest.raises(backup.BackupError, match="there is no backup to verify"):
        backup.verify(warehouse)


def test_a_restore_forgets_the_local_state_of_the_pipelines_only(tmp_path: Path) -> None:
    for name in ("ga4", "meta_ads", "twenty", "someone_elses_pipeline"):
        (tmp_path / name / "state").mkdir(parents=True)
    (tmp_path / "stripe").write_text("a file, not a pipeline directory")
    assert backup_cli.forget_local_state(tmp_path) == ["meta_ads", "ga4", "twenty"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["someone_elses_pipeline", "stripe"]


def test_the_cli_reports_a_refusal_as_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, tmp_path: Path
) -> None:
    from omw import cli

    warehouse = BackupWarehouse()
    monkeypatch.setattr(backup_cli, "pipeline_client", lambda: warehouse)
    assert cli.main(["backup", "restore", "omw-20261118T060000Z.zip"]) == 1
    assert cli.main(["backup", "create", "--name", "manual.zip"]) == 0
    assert "manual.zip: 3 tables and views, 72 rows, 830,735 bytes" in capsys.readouterr().out
    monkeypatch.delenv("OMW_BACKUP_DIR", raising=False)
    assert cli.main(["backup", "prune"]) == 1  # the files are not visible from here: say so, delete nothing
    (tmp_path / "omw-20261101T060000Z.zip").write_bytes(b"x")
    (tmp_path / "omw-20261102T060000Z.zip").write_bytes(b"x")
    assert cli.main(["backup", "prune", "--keep", "1", "--dir", str(tmp_path)]) == 0
    assert [path.name for path in tmp_path.iterdir()] == ["omw-20261102T060000Z.zip"]
