"""The scheduler loop end to end, in fixture mode: every job on its cadence through the real pipelines into
ClickHouse, the post-sync checks after every sync, the run log, and the hook and exit code on failure."""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from clickhouse_connect.driver.client import Client

from omw import cli
from omw.demo import warehouse
from omw.demo.cli import first_load
from omw.demo.fixtures import SOURCES, Manifest, generate
from omw.scheduler.jobs import Job, build_plan, job_named
from omw.scheduler.loop import Scheduler, subprocess_launcher
from omw.scheduler.modes import FixtureMode
from omw.scheduler.notify import AlertConfig, Notifier
from omw.scheduler.runs import RunStore
from omw.scheduler.sync import run_job

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 18)
DAYS = 60
TABLE_OF_FILE = {
    "campaign_entities": "campaigns",
    "campaigns_stream": "performance_campaigns",
    "ad_groups_stream": "performance_ad_groups",
    "search_terms_stream": "performance_search_terms",
    "asset_groups_stream": "performance_asset_groups",
}


def coming_sunday_noon() -> datetime:
    """A Sunday after every slot of the default schedule, in the near future: the run log keeps 400 days."""
    now = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    return now + timedelta(days=(6 - now.weekday()) % 7 or 7)


@dataclass
class Hook:
    """A webhook receiver on localhost: what the failure hook posted."""

    url: str
    received: list[dict[str, Any]] = field(default_factory=list)

    def kinds(self) -> list[tuple[str, str]]:
        return [(body["kind"], body["job"]) for body in self.received]


@pytest.fixture(scope="module")
def hook() -> Iterator[Hook]:
    found = Hook("")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
            found.received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_: Any) -> None:
            return None

    server = HTTPServer(("127.0.0.1", 0), Handler)
    found.url = f"http://127.0.0.1:{server.server_port}/hook"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield found
    server.shutdown()
    thread.join(timeout=5)


@dataclass
class Stack:
    out: Path
    manifest: Manifest
    sunday: datetime
    hook: Hook

    def mode(self, directory: Path | None = None, **expiries: Any) -> FixtureMode:
        return FixtureMode(directory or self.out, expiries)

    def notifier(self) -> Notifier:
        return Notifier(AlertConfig(webhook_url=self.hook.url))

    def scheduler(self, now: datetime, mode: FixtureMode | None = None, retries: int = 0) -> Scheduler:
        """The loop with each job run in this process, as ``omw sync`` runs it in its own."""
        mode = mode or self.mode()
        notifier = self.notifier()

        def launch(job: Job, slot: datetime, retries_left: int = 0) -> int:
            return run_job(job, mode, notifier, slot, "schedule", retries_left=retries_left).exit_code

        return Scheduler(
            build_plan(),
            launch,
            lambda: RunStore(mode.client()),
            notifier,
            clock=lambda: now,
            retries=retries,
            retry_after=timedelta(seconds=0),
        )


@pytest.fixture(scope="module")
def stack(pipeline_client: Client, tmp_path_factory: pytest.TempPathFactory, hook: Hook) -> Iterator[Stack]:
    """A warehouse holding the demo's first load (the whole history as of yesterday), and no run log."""
    out = tmp_path_factory.mktemp("scheduler")
    manifest = generate(out, TODAY, DAYS)
    warehouse.reset(pipeline_client, force=True)
    for table in ("ops.sync_runs", "ops.backup_manifest", "ops.backup_log"):
        pipeline_client.command(f"DROP TABLE IF EXISTS {table}")
    first_load(out, manifest, str(tmp_path_factory.mktemp("dlt")), force=False)
    yield Stack(out, manifest, coming_sunday_noon(), hook)
    warehouse.reset(pipeline_client, force=True)
    for table in ("ops.sync_runs", "ops.backup_manifest", "ops.backup_log"):
        pipeline_client.command(f"DROP TABLE IF EXISTS {table}")


def runs(client: Client, where: str = "1") -> list[tuple[Any, ...]]:
    found: Any = client.query(
        "SELECT job, status, trigger, rows, checks_failed, detail, toUnixTimestamp(slot), "
        f"toUnixTimestamp(started_at) FROM ops.sync_runs WHERE {where} ORDER BY started_at, job"
    ).result_rows
    return [tuple(row) for row in found]


def test_a_first_tick_runs_every_job_and_checks_after_every_sync(
    stack: Stack, pipeline_client: Client
) -> None:
    codes = stack.scheduler(stack.sunday).tick()
    assert codes == dict.fromkeys(
        [
            "search_console",
            "meta_ads_weekly",
            "meta_ads_daily",
            "google_ads",
            "ga4",
            "stripe",
            "linkedin_ads",
            "twenty",
            "backup",
            "backup_verify",
        ],
        0,
    )
    logged = {row[0]: row for row in runs(pipeline_client)}
    assert set(logged) == set(codes)
    assert logged["meta_ads_daily"][1:3] == ("skipped", "schedule")
    assert logged["meta_ads_daily"][5] == "covered by meta_ads_weekly"
    for job, row in logged.items():
        if job != "meta_ads_daily":
            assert row[1:3] == ("ok", "schedule") and row[3] > 0 and row[4] == 0, row
    assert logged["meta_ads_weekly"][6] == int((stack.sunday.replace(hour=3, minute=15)).timestamp())
    assert stack.hook.received == []

    # Every source was checked after its own sync, and only then: one freshness verdict each, all passing.
    verdicts = pipeline_client.query(
        "SELECT source, count(), countIf(status = 'pass'), toUnixTimestamp(max(checked_at)) "
        "FROM ops.check_results WHERE check = 'freshness' GROUP BY source"
    ).result_rows
    assert {row[0]: row[1:3] for row in verdicts} == dict.fromkeys(SOURCES, (1, 1))
    failed = pipeline_client.query("SELECT count() FROM ops.check_results WHERE status = 'fail'").result_rows
    assert failed[0][0] == 0
    kinds = pipeline_client.query("SELECT DISTINCT check FROM ops.check_results").result_rows
    assert {row[0] for row in kinds} == {"freshness", "overcount", "rowcount"}


def test_the_syncs_left_the_warehouse_equal_to_the_restated_snapshot(
    stack: Stack, pipeline_client: Client
) -> None:
    """Each job was an ordinary daily sync (Meta: the 28-day reconcile) over the first load: the re-fetched
    slices replaced what was there, row for row."""
    for source, files in stack.manifest.data["records"]["restated"].items():
        for name, rows in files.items():
            if name == "account":
                continue
            table = f"raw.{source}___{TABLE_OF_FILE.get(name, name)}"
            assert pipeline_client.query(f"SELECT count() FROM {table}").result_rows[0][0] == rows, table


def test_the_backup_job_made_a_backup_and_the_drill_restored_it(
    stack: Stack, pipeline_client: Client
) -> None:
    events = pipeline_client.query(
        "SELECT event, backup, tables, rows FROM ops.backup_log ORDER BY at, event"
    ).result_rows
    assert [row[0] for row in events] == ["created", "verified"]
    assert events[0][1] == events[1][1] and events[0][2] > 100 and events[1][3] >= events[0][3] > 10_000
    leftovers = pipeline_client.query(
        "SELECT count() FROM system.databases WHERE name LIKE 'omw_drill_%'"
    ).result_rows
    assert leftovers[0][0] == 0


def test_a_slot_runs_once_and_monday_brings_the_daily_jobs_back(
    stack: Stack, pipeline_client: Client
) -> None:
    before = len(runs(pipeline_client))
    assert stack.scheduler(stack.sunday + timedelta(hours=3)).tick() == {}
    assert len(runs(pipeline_client)) == before
    monday = stack.sunday + timedelta(days=1)
    only = [
        planned
        for planned in build_plan()
        if planned.job.name in ("meta_ads_daily", "twenty", "meta_ads_weekly")
    ]
    scheduler = stack.scheduler(monday)
    scheduler.plan = only
    assert scheduler.tick() == {"meta_ads_daily": 0, "twenty": 0}  # the weekly reconcile waits for Sunday
    monday_runs = runs(pipeline_client, f"slot >= toDateTime({int(monday.replace(hour=0).timestamp())})")
    assert [(row[0], row[1]) for row in monday_runs] == [("meta_ads_daily", "ok"), ("twenty", "ok")]
    assert stack.hook.received == []


def test_a_sync_that_fails_fires_the_hook_exits_1_and_the_next_job_still_runs(
    stack: Stack, pipeline_client: Client, tmp_path: Path
) -> None:
    broken = tmp_path / "broken"
    shutil.copytree(stack.out, broken)
    (broken / "restated" / "stripe" / "customers.json").unlink()  # Stripe stops answering for customers
    tuesday = stack.sunday + timedelta(days=2)
    scheduler = stack.scheduler(tuesday, stack.mode(broken))
    scheduler.plan = [planned for planned in build_plan() if planned.job.name in ("stripe", "linkedin_ads")]
    stack.hook.received.clear()
    assert scheduler.tick() == {"stripe": 1, "linkedin_ads": 0}
    assert scheduler.run(once=True) == 0  # the failed slot was attempted: nothing is due, nothing is retried

    assert stack.hook.kinds() == [("sync_failed", "stripe")]
    alert = stack.hook.received[0]
    assert alert["severity"] == "error" and alert["source"] == "stripe"
    assert alert["text"].startswith("[omw] ERROR stripe on ") and alert["summary"]
    since = f"slot >= toDateTime({int(tuesday.replace(hour=0).timestamp())})"
    logged = {row[0]: row for row in runs(pipeline_client, since)}
    assert logged["stripe"][1] == "failed" and logged["stripe"][5] == alert["summary"]
    assert logged["linkedin_ads"][1] == "ok"
    # The checks ran after the failed sync too: Stripe has a newer freshness verdict than its last load.
    checked = pipeline_client.query(
        "SELECT count() FROM ops.check_results WHERE source = 'stripe' AND check = 'freshness'"
    ).result_rows[0][0]
    assert checked == 2


def test_a_failed_sync_is_retried_quietly_and_the_hook_fires_after_the_last_attempt(
    stack: Stack, pipeline_client: Client, tmp_path: Path
) -> None:
    broken = tmp_path / "broken"
    shutil.copytree(stack.out, broken)
    (broken / "restated" / "stripe" / "customers.json").unlink()
    thursday = stack.sunday + timedelta(days=4)
    since = f"job = 'stripe' AND slot >= toDateTime({int(thursday.replace(hour=0).timestamp())})"
    only = [planned for planned in build_plan() if planned.job.name == "stripe"]
    stack.hook.received.clear()

    # An outage that ends: the first attempt fails without a word, the second one loads.
    failing = stack.scheduler(thursday, stack.mode(broken), retries=2)
    failing.plan = only
    assert failing.tick() == {"stripe": 1} and stack.hook.received == []
    recovered = stack.scheduler(thursday, retries=2)  # a restarted loop: the run log alone says "retry"
    recovered.plan = only
    assert recovered.tick() == {"stripe": 0} and recovered.tick() == {}
    assert [row[1] for row in runs(pipeline_client, since)] == ["failed", "ok"]
    assert stack.hook.received == []

    # An outage that does not: three attempts, one alert.
    friday = thursday + timedelta(days=1)
    dead = stack.scheduler(friday, stack.mode(broken), retries=2)
    dead.plan = only
    assert [dead.tick() for _ in range(4)] == [{"stripe": 1}, {"stripe": 1}, {"stripe": 1}, {}]
    assert stack.hook.kinds() == [("sync_failed", "stripe")]
    later = f"job = 'stripe' AND slot >= toDateTime({int(friday.replace(hour=0).timestamp())})"
    assert [row[1] for row in runs(pipeline_client, later)] == ["failed", "failed", "failed"]


def test_a_failed_post_sync_check_fires_the_hook_and_exits_3(
    stack: Stack, pipeline_client: Client, agent_client: Client, tmp_path: Path
) -> None:
    faulty = tmp_path / "faulty"
    generate(faulty, TODAY, DAYS, fault="overcount")  # GA4's channel grain exceeds the property total
    stack.hook.received.clear()
    outcome = run_job(job_named("ga4"), stack.mode(faulty), stack.notifier())
    assert (str(outcome.status), outcome.exit_code, outcome.checks_failed) == ("check_failed", 3, 1)
    assert stack.hook.kinds() == [("check_failed", "ga4")]
    (detail,) = stack.hook.received[0]["details"]
    assert detail.startswith("FAIL ga4/overcount/channels: 3 of ")
    row = runs(pipeline_client, "job = 'ga4' AND trigger = 'manual'")[-1]
    assert row[1] == "check_failed" and row[3] > 0 and row[4] == 1
    # The agent sees the verdict the scheduler wrote.
    verdict = agent_client.query(
        "SELECT status FROM ops.check_verdicts "
        "WHERE source = 'ga4' AND check = 'overcount' AND report = 'channels'"
    ).result_rows
    assert verdict == [("fail",)]
    # The next good sync replaces the slice and clears it.
    again = run_job(job_named("ga4"), stack.mode(), stack.notifier())
    assert again.exit_code == 0 and len(stack.hook.received) == 1


def test_a_credential_about_to_expire_warns_and_an_expired_one_fails(stack: Stack) -> None:
    today = datetime.now(tz=UTC).date()
    stack.hook.received.clear()
    soon = run_job(
        job_named("twenty"), stack.mode(twenty=(today + timedelta(days=3)).isoformat()), stack.notifier()
    )
    assert soon.exit_code == 0 and soon.checks_warned == 1
    assert stack.hook.kinds() == [("expiry_warning", "twenty")]
    assert stack.hook.received[0]["severity"] == "warning"
    assert "the API key expires on" in stack.hook.received[0]["summary"]
    gone = run_job(
        job_named("twenty"), stack.mode(twenty=(today - timedelta(days=1)).isoformat()), stack.notifier()
    )
    assert gone.exit_code == 3 and stack.hook.kinds()[-1] == ("check_failed", "twenty")
    assert "FAIL twenty/expiry/token: the API key expired on" in stack.hook.received[-1]["details"][0]
    assert run_job(job_named("twenty"), stack.mode(), stack.notifier()).exit_code == 0


def test_each_job_runs_as_its_own_omw_sync_process(stack: Stack, pipeline_client: Client) -> None:
    """The loop's real launcher: ``python -m omw.cli sync <job> --slot ... --fixtures ...``."""
    slot = stack.sunday + timedelta(days=3, hours=-7)
    launch = subprocess_launcher(stack.mode().child_args(), timeout_minutes=5)
    assert launch(job_named("ga4"), slot, 0) == 0
    row = runs(pipeline_client, f"job = 'ga4' AND slot = toDateTime({int(slot.timestamp())})")
    assert len(row) == 1 and row[0][1:3] == ("ok", "schedule") and row[0][3] > 0
    assert (
        launch(job_named("ga4"), slot, 2) == 0
    )  # a process, so nothing of the first run is left in this one
    missing = subprocess_launcher(["--fixtures", str(stack.out / "nowhere")], timeout_minutes=5)
    assert missing(job_named("ga4"), slot, 0) == 2  # not an exit code `omw sync` uses for a job it handled


def test_schedule_run_once_from_the_cli(stack: Stack, pipeline_client: Client, capsys: Any) -> None:
    assert cli.main(["schedule", "run", "--once", "--fixtures", str(stack.out), "--job", "twenty"]) == 0
    assert cli.main(["schedule", "list", "--fixtures", str(stack.out)]) == 0
    listed = capsys.readouterr().out
    assert "twenty" in listed and "meta_ads_weekly" in listed and "sun 03:15" in listed
    assert cli.main(["schedule", "run", "--once", "--fixtures", str(stack.out), "--job", "nope"]) == 2


def test_fixture_mode_refuses_a_warehouse_it_did_not_load(
    stack: Stack, pipeline_client: Client, admin_client: Client
) -> None:
    admin_client.command(
        "INSERT INTO raw.`stripe____dlt_loads` (load_id, schema_name, status, inserted_at) "
        "VALUES ('9999999999.1', 'stripe', 0, now())"
    )
    stack.hook.received.clear()
    try:
        before = pipeline_client.query("SELECT count() FROM raw.ga4___core").result_rows[0][0]
        outcome = run_job(job_named("ga4"), stack.mode(), stack.notifier())
        assert outcome.exit_code == 1 and "DemoRefusedError" in outcome.detail
        assert stack.hook.kinds() == [("sync_failed", "ga4")]
        assert pipeline_client.query("SELECT count() FROM raw.ga4___core").result_rows[0][0] == before
    finally:
        admin_client.command("DELETE FROM raw.`stripe____dlt_loads` WHERE load_id = '9999999999.1'")
