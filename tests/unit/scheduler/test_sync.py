"""One job: the checks run after every sync, the run is logged, the hook fires, the exit code says which."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omw import backup
from omw.checks.results import CheckResult, Status
from omw.checks.runner import CheckRun
from omw.scheduler import cli as scheduler_cli
from omw.scheduler import sync
from omw.scheduler.jobs import job_named
from omw.scheduler.notify import Notifier
from omw.scheduler.runs import RUN_COLUMNS, SYNC_RUNS, RunStatus
from omw.scheduler.sync import run_job
from tests.unit.checks.conftest import FakeWarehouse
from tests.unit.scheduler.conftest import kinds

SLOT = datetime(2026, 11, 18, 4, 15, tzinfo=UTC)
NOW = datetime(2026, 11, 18, 12, 0)


class FakeMode:
    name = "fake"

    def __init__(self, warehouse: FakeWarehouse, error: Exception | None = None) -> None:
        self.warehouse = warehouse
        self.error = error
        self.synced: list[str] = []
        self.expiry: dict[str, Any] = {}

    def client(self) -> Any:
        return self.warehouse

    def sync(self, job: Any) -> dict[str, int]:
        self.synced.append(job.name)
        if self.error is not None:
            raise self.error
        return {"core": 7, "channels": 21, "_dlt_pipeline_state": 1}

    def today(self) -> date | None:
        return None

    def budgets(self) -> dict[str, float] | None:
        return {"ga4": 30.0, "stripe": 30.0}

    def expiries(self, source: str) -> dict[str, Any]:
        return {source: self.expiry.get(source)}

    def child_args(self) -> list[str]:
        return []


@pytest.fixture
def ga4_warehouse(warehouse: FakeWarehouse) -> FakeWarehouse:
    """GA4 synced an hour ago; no marts views, so only freshness has something to say."""
    warehouse.objects = {"raw.ga4___sync_windows"}
    warehouse.when(
        "raw.ga4___sync_windows", [[datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(hours=1), 3]]
    )
    return warehouse


def run_row(warehouse: FakeWarehouse) -> dict[str, Any]:
    table, rows, columns = next(entry for entry in warehouse.inserted if entry[0] == SYNC_RUNS)
    assert tuple(columns) == RUN_COLUMNS and len(rows) == 1
    return dict(zip(columns, rows[0], strict=True))


def test_a_good_sync_is_checked_logged_and_silent(ga4_warehouse: FakeWarehouse, notifier: Notifier) -> None:
    mode = FakeMode(ga4_warehouse)
    outcome = run_job(job_named("ga4"), mode, notifier, SLOT, "schedule")
    assert (outcome.status, outcome.exit_code, outcome.rows) == (RunStatus.OK, 0, 28)
    assert mode.synced == ["ga4"] and notifier.sent == []
    # The post-sync checks ran, for this source only, and were written.
    checks = [entry for entry in ga4_warehouse.inserted if entry[0] == "ops.check_results"]
    assert [row[1:5] for row in checks[0][1]] == [["ga4", "freshness", "sync", "pass"]]
    row = run_row(ga4_warehouse)
    assert (row["job"], row["source"], row["status"], row["trigger"], row["rows"]) == (
        "ga4",
        "ga4",
        "ok",
        "schedule",
        28,
    )
    assert row["slot"] == SLOT and row["checks_failed"] == 0 and row["detail"] == "28 rows"
    assert any("CREATE TABLE IF NOT EXISTS ops.sync_runs" in command for command in ga4_warehouse.commands)


def test_a_failed_sync_still_runs_the_checks_then_alerts_and_exits_1(
    warehouse: FakeWarehouse, notifier: Notifier
) -> None:
    warehouse.objects = {"raw.stripe___sync_windows"}
    warehouse.when("raw.stripe___sync_windows", [[NOW - timedelta(days=3), 40]])
    mode = FakeMode(warehouse, PermissionError("Invalid API Key provided\nrequest id req_synthetic"))
    outcome = run_job(job_named("stripe"), mode, notifier)
    assert (outcome.status, outcome.exit_code) == (RunStatus.FAILED, 1)
    checks = next(entry for entry in warehouse.inserted if entry[0] == "ops.check_results")
    assert checks[1][0][1:5] == [
        "stripe",
        "freshness",
        "sync",
        "fail",
    ]  # the verdict moved although nothing loaded
    assert kinds(notifier) == [("sync_failed", "stripe")]
    alert = notifier.sent[0]
    assert alert.summary == "PermissionError: Invalid API Key provided" and alert.severity == "error"
    assert len(alert.details) == 1 and alert.details[0].startswith("FAIL stripe/freshness/sync: last sync")
    row = run_row(warehouse)
    assert (row["status"], row["trigger"], row["checks_failed"]) == ("failed", "manual", 1)
    assert row["detail"].startswith("PermissionError: Invalid API Key provided; FAIL stripe/freshness/sync")


def test_a_failed_sync_stays_quiet_while_the_loop_will_try_again(
    warehouse: FakeWarehouse, notifier: Notifier
) -> None:
    warehouse.objects = {"raw.stripe___sync_windows"}
    warehouse.when("raw.stripe___sync_windows", [[datetime.now(tz=UTC).replace(tzinfo=None), 40]])
    mode = FakeMode(warehouse, ConnectionError("503 Service Unavailable"))
    outcome = run_job(job_named("stripe"), mode, notifier, retries_left=2)
    assert (outcome.status, outcome.exit_code) == (RunStatus.FAILED, 1)  # recorded and reported to the loop
    assert notifier.sent == [] and run_row(warehouse)["status"] == "failed"
    last = run_job(job_named("stripe"), mode, notifier, retries_left=0)
    assert last.exit_code == 1 and kinds(notifier) == [("sync_failed", "stripe")]


def test_a_failed_check_is_reported_at_once_whatever_retries_are_left(
    ga4_warehouse: FakeWarehouse, notifier: Notifier
) -> None:
    mode = FakeMode(ga4_warehouse)
    mode.expiry = {"ga4": (datetime.now(tz=UTC).date() - timedelta(days=1)).isoformat()}
    outcome = run_job(job_named("ga4"), mode, notifier, retries_left=2)
    assert outcome.exit_code == 3 and kinds(notifier) == [("check_failed", "ga4")]


def test_a_failed_check_after_a_good_sync_exits_3(
    ga4_warehouse: FakeWarehouse, notifier: Notifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(_client: Any, **_: Any) -> CheckRun:
        return CheckRun(
            [
                CheckResult("ga4", "freshness", "sync", Status.PASS, 1.0, 30.0, "last sync 1.0h ago"),
                CheckResult(
                    "ga4", "overcount", "channels", Status.FAIL, 2.0, 1.01, "3 of 60 groups exceed core"
                ),
                CheckResult(
                    "ga4", "rowcount", "core", Status.WARN, 7.0, 60.0, "7 rows vs 60 the window before"
                ),
            ]
        )

    monkeypatch.setattr(sync, "run_checks", failing)
    outcome = run_job(job_named("ga4"), FakeMode(ga4_warehouse), notifier)
    assert (outcome.status, outcome.exit_code) == (RunStatus.CHECK_FAILED, 3)
    assert (outcome.checks_failed, outcome.checks_warned) == (1, 1)
    assert kinds(notifier) == [("check_failed", "ga4")]
    assert notifier.sent[0].details == ("FAIL ga4/overcount/channels: 3 of 60 groups exceed core",)
    assert run_row(ga4_warehouse)["status"] == "check_failed"


def test_checks_that_cannot_run_are_a_failed_check(
    ga4_warehouse: FakeWarehouse, notifier: Notifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_client: Any, **_: Any) -> CheckRun:
        raise ConnectionError("warehouse went away")

    monkeypatch.setattr(sync, "run_checks", broken)
    outcome = run_job(job_named("ga4"), FakeMode(ga4_warehouse), notifier)
    assert outcome.exit_code == 3 and kinds(notifier) == [("check_failed", "ga4")]
    assert "the post-sync checks could not run: ConnectionError: warehouse went away" in outcome.detail


def test_a_credential_about_to_expire_warns_without_failing_the_job(
    ga4_warehouse: FakeWarehouse, notifier: Notifier
) -> None:
    mode = FakeMode(ga4_warehouse)
    mode.expiry = {"ga4": (datetime.now(tz=UTC).date() + timedelta(days=5)).isoformat()}
    outcome = run_job(job_named("ga4"), mode, notifier)
    assert (outcome.status, outcome.exit_code, outcome.checks_warned) == (RunStatus.OK, 0, 1)
    assert kinds(notifier) == [("expiry_warning", "ga4")]
    assert notifier.sent[0].severity == "warning"
    assert (
        "service-account key expires on" in notifier.sent[0].summary
        and "(in 5 days)" in notifier.sent[0].summary
    )
    notifier.sent.clear()
    mode.expiry = {"ga4": (datetime.now(tz=UTC).date() - timedelta(days=1)).isoformat()}
    expired = run_job(job_named("ga4"), mode, notifier)
    assert expired.exit_code == 3 and kinds(notifier) == [("check_failed", "ga4")]
    assert "service-account key expired on" in notifier.sent[0].details[0]


def test_the_hourly_checks_alert_when_a_check_starts_failing_and_not_again(
    warehouse: FakeWarehouse, notifier: Notifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stripe synced three days ago and nothing has told anyone since; GA4 is fine."""
    warehouse.objects = {"raw.ga4___sync_windows", "raw.stripe___sync_windows", "ops.check_verdicts"}
    fresh = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(hours=1)
    warehouse.when("raw.ga4___sync_windows", [[fresh, 3]])
    warehouse.when("raw.stripe___sync_windows", [[fresh - timedelta(days=3), 40]])
    failing: list[list[str]] = []
    warehouse.answers.append((lambda sql: "FROM ops.check_verdicts" in sql, failing))
    monkeypatch.setattr(sync, "checked_sources", lambda budgets: (budgets, {"ga4", "stripe"}))
    mode = FakeMode(warehouse)

    outcome = run_job(job_named("checks"), mode, notifier, SLOT, "schedule")
    assert (outcome.status, outcome.exit_code, outcome.rows) == (RunStatus.CHECK_FAILED, 3, 2)
    assert mode.synced == []  # it checks; it loads nothing
    assert kinds(notifier) == [("check_failed", "checks")]
    alert = notifier.sent[0]
    assert alert.summary == "1 check(s) started failing between syncs"
    assert len(alert.details) == 1 and alert.details[0].startswith("FAIL stripe/freshness/sync: last sync 7")
    row = run_row(warehouse)
    assert (row["job"], row["source"], row["status"], row["checks_failed"]) == (
        "checks",
        "",
        "check_failed",
        1,
    )
    assert row["detail"].startswith("1 pass, 0 warn, 1 fail: FAIL stripe/freshness/sync")

    failing.append(["stripe", "freshness", "sync"])  # what the first run wrote
    notifier.sent.clear()
    again = run_job(job_named("checks"), mode, notifier, SLOT, "schedule")
    assert again.exit_code == 3 and notifier.sent == []  # still failing, already said


def test_the_hourly_checks_are_quiet_when_everything_passes(
    ga4_warehouse: FakeWarehouse, notifier: Notifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    ga4_warehouse.objects |= {"ops.check_verdicts"}
    ga4_warehouse.when("FROM ops.check_verdicts", [])
    monkeypatch.setattr(sync, "checked_sources", lambda budgets: ({"ga4": 30.0}, {"ga4"}))
    outcome = run_job(job_named("checks"), FakeMode(ga4_warehouse), notifier)
    assert (outcome.status, outcome.exit_code, outcome.detail) == (RunStatus.OK, 0, "1 pass, 0 warn, 0 fail")
    assert notifier.sent == []
    written = next(entry for entry in ga4_warehouse.inserted if entry[0] == "ops.check_results")
    assert {row[1] for row in written[1]} == {"ga4"}  # a source that is not scheduled is not checked


def test_no_warehouse_is_a_failed_job_with_an_alert(warehouse: FakeWarehouse, notifier: Notifier) -> None:
    class Unreachable(FakeMode):
        def client(self) -> Any:
            raise ConnectionError("connection refused")

    outcome = run_job(job_named("twenty"), Unreachable(warehouse), notifier)
    assert outcome.exit_code == 1 and kinds(notifier) == [("sync_failed", "twenty")]
    assert warehouse.inserted == []


def test_the_backup_job_backs_up_then_prunes(
    warehouse: FakeWarehouse, notifier: Notifier, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        "omw-20261101T060000Z.zip",
        "omw-20261102T060000Z.zip",
        "omw-20261103T060000Z.zip",
        "named.zip",
    ):
        (tmp_path / name).write_bytes(b"zip")
    monkeypatch.setenv("OMW_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(sync, "backup_keep", lambda: 2)
    monkeypatch.setattr(
        backup, "create", lambda client: backup.Backup("omw-20261103T060000Z.zip", 181, 16903, 830735)
    )
    mode = FakeMode(warehouse)
    outcome = run_job(job_named("backup"), mode, notifier)
    assert outcome.exit_code == 0 and mode.synced == [] and notifier.sent == []
    assert outcome.detail == (
        "omw-20261103T060000Z.zip: 181 tables, 16,903 rows, 830,735 bytes; pruned omw-20261101T060000Z.zip"
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "named.zip",
        "omw-20261102T060000Z.zip",
        "omw-20261103T060000Z.zip",
    ]
    assert not [
        entry for entry in warehouse.inserted if entry[0] == "ops.check_results"
    ]  # no source, no checks
    assert run_row(warehouse)["rows"] == 16903


def test_a_failed_restore_drill_is_a_failed_job(
    warehouse: FakeWarehouse, notifier: Notifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    drill = backup.Drill(
        "omw-20261103T060000Z.zip", 180, 16000, ["raw.ga4___core: 6 rows restored, the manifest says 7"]
    )
    monkeypatch.setattr(backup, "verify", lambda client: drill)
    outcome = run_job(job_named("backup_verify"), FakeMode(warehouse), notifier)
    assert outcome.exit_code == 1 and kinds(notifier) == [("backup_failed", "backup_verify")]
    assert "raw.ga4___core: 6 rows restored, the manifest says 7" in notifier.sent[0].summary
    monkeypatch.setattr(backup, "verify", lambda client: backup.Drill("omw-20261103T060000Z.zip", 181, 16903))
    assert run_job(job_named("backup_verify"), FakeMode(warehouse), notifier).exit_code == 0


def test_the_cli_runs_a_job_and_returns_its_exit_code(
    ga4_warehouse: FakeWarehouse, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omw import cli

    mode = FakeMode(ga4_warehouse)
    monkeypatch.setattr(scheduler_cli, "mode_of", lambda args: mode)
    assert cli.main(["sync", "ga4", "--slot", SLOT.isoformat(), "--trigger", "timer"]) == 0
    row = run_row(ga4_warehouse)
    assert (row["slot"], row["trigger"]) == (SLOT, "timer")
    mode.error = RuntimeError("boom")
    assert cli.main(["sync", "ga4"]) == 1
    assert cli.main(["sync", "nonsense"]) == 2
