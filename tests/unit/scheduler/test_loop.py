"""The loop: what is due, in which order, once per slot, and what it does when a job cannot report itself."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from omw.scheduler.jobs import Job, build_plan
from omw.scheduler.loop import EXIT_TIMED_OUT, Scheduler, beat
from omw.scheduler.notify import Notifier
from omw.scheduler.runs import EXIT_CHECK_FAILED, EXIT_FAILED, Outcome, RunStatus
from tests.unit.scheduler.conftest import MemoryStore, kinds

SUNDAY_NOON = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
SYNCS = ["search_console", "meta_ads_weekly", "google_ads", "ga4", "stripe", "linkedin_ads", "twenty"]


class Launcher:
    """Stands in for ``omw sync``: records the run like the real process does, with a scripted exit code."""

    def __init__(self, store: MemoryStore, codes: dict[str, int] | None = None, silent: bool = False) -> None:
        self.store = store
        self.codes = codes or {}
        self.silent = silent
        self.started: list[tuple[str, datetime]] = []

    def __call__(self, job: Job, slot: datetime) -> int:
        self.started.append((job.name, slot))
        code = self.codes.get(job.name, 0)
        status = {0: RunStatus.OK, EXIT_FAILED: RunStatus.FAILED, EXIT_CHECK_FAILED: RunStatus.CHECK_FAILED}
        if code in status and not self.silent:
            self.store.record(Outcome(job.name, job.source, status[code], slot, slot, slot, "schedule"))
        return code


def scheduler(store: MemoryStore, launcher: Launcher, notifier: Notifier, now: datetime) -> Scheduler:
    return Scheduler(build_plan(), launcher, lambda: store, notifier, clock=lambda: now)  # type: ignore[arg-type,return-value]


def test_a_first_start_runs_every_job_once_in_order(store: MemoryStore, notifier: Notifier) -> None:
    launcher = Launcher(store)
    codes = scheduler(store, launcher, notifier, SUNDAY_NOON).tick()
    assert [name for name, _ in launcher.started] == [*SYNCS, "backup", "backup_verify"]
    assert set(codes.values()) == {0} and notifier.sent == []
    slots = dict(launcher.started)
    assert slots["search_console"] == datetime(2026, 11, 22, 3, 0, tzinfo=UTC)
    assert slots["meta_ads_weekly"] == datetime(2026, 11, 22, 3, 15, tzinfo=UTC)
    # Saturday's daily re-pull was never attempted either, and the weekly reconcile covers it.
    assert ("meta_ads_daily", "skipped") in store.statuses()
    skipped = next(o for o in store.outcomes if o.job == "meta_ads_daily")
    assert skipped.detail == "covered by meta_ads_weekly" and skipped.exit_code == 0


def test_a_slot_runs_once_and_the_next_day_brings_the_daily_jobs_back(
    store: MemoryStore, notifier: Notifier
) -> None:
    launcher = Launcher(store)
    assert scheduler(store, launcher, notifier, SUNDAY_NOON).tick()
    first = len(launcher.started)
    # A restarted process has no memory of its own: the run log alone says the slots are done.
    assert scheduler(store, launcher, notifier, SUNDAY_NOON + timedelta(hours=6)).tick() == {}
    assert len(launcher.started) == first
    monday = scheduler(store, launcher, notifier, SUNDAY_NOON + timedelta(days=1))
    assert list(monday.tick()) == [
        "search_console",
        "meta_ads_daily",
        "google_ads",
        "ga4",
        "stripe",
        "linkedin_ads",
        "twenty",
        "backup",
    ]


def test_a_job_becomes_due_at_its_slot_not_before(store: MemoryStore, notifier: Notifier) -> None:
    launcher = Launcher(store)
    scheduler(store, launcher, notifier, SUNDAY_NOON).tick()
    launcher.started.clear()
    before = datetime(2026, 11, 23, 2, 59, 59, tzinfo=UTC)
    assert scheduler(store, launcher, notifier, before).tick() == {}
    assert list(scheduler(store, launcher, notifier, before + timedelta(seconds=1)).tick()) == [
        "search_console"
    ]


def test_days_of_downtime_are_caught_up_once(store: MemoryStore, notifier: Notifier) -> None:
    launcher = Launcher(store)
    scheduler(store, launcher, notifier, SUNDAY_NOON).tick()
    launcher.started.clear()
    thursday = SUNDAY_NOON + timedelta(days=4)
    scheduler(store, launcher, notifier, thursday).tick()
    ga4 = [slot for name, slot in launcher.started if name == "ga4"]
    assert ga4 == [datetime(2026, 11, 26, 4, 15, tzinfo=UTC)]  # the latest slot, not one per missed day


def test_a_failed_job_does_not_stop_the_ones_after_it(store: MemoryStore, notifier: Notifier) -> None:
    launcher = Launcher(store, {"ga4": EXIT_FAILED, "stripe": EXIT_CHECK_FAILED})
    loop = scheduler(store, launcher, notifier, SUNDAY_NOON)
    codes = loop.tick()
    assert codes["ga4"] == 1 and codes["stripe"] == 3 and codes["twenty"] == 0
    assert notifier.sent == []  # `omw sync` reported those itself; the loop adds nothing
    assert loop.tick() == {}  # and a failed slot is not retried until the next one
    assert scheduler(store, Launcher(store, {"ga4": EXIT_FAILED}), notifier, SUNDAY_NOON).run(once=True) == 0
    late = SUNDAY_NOON + timedelta(days=1)
    assert scheduler(store, Launcher(store, {"ga4": EXIT_FAILED}), notifier, late).run(once=True) == 1


def test_the_loop_reports_a_job_that_died_or_ran_out_of_time(store: MemoryStore, notifier: Notifier) -> None:
    launcher = Launcher(store, {"google_ads": -9, "twenty": EXIT_TIMED_OUT})
    codes = scheduler(store, launcher, notifier, SUNDAY_NOON).tick()
    assert codes["google_ads"] == -9
    assert ("google_ads", "crashed") in store.statuses() and ("twenty", "crashed") in store.statuses()
    assert kinds(notifier) == [("job_crashed", "google_ads"), ("job_crashed", "twenty")]
    assert notifier.sent[0].summary == "its process ended with code -9"
    assert notifier.sent[1].summary == "ran out of time"


def test_a_job_that_cannot_be_started_is_reported_and_the_loop_goes_on(
    store: MemoryStore, notifier: Notifier
) -> None:
    class NoFork(Launcher):
        def __call__(self, job: Job, slot: datetime) -> int:
            if job.name == "ga4":
                raise OSError("cannot allocate memory")
            return super().__call__(job, slot)

    codes = scheduler(store, NoFork(store), notifier, SUNDAY_NOON).tick()
    assert codes["ga4"] == 127 and codes["stripe"] == 0
    assert ("ga4", "crashed") in store.statuses()
    assert [(a.kind, a.job, a.summary) for a in notifier.sent] == [
        ("job_crashed", "ga4", "its process could not be started")
    ]


def test_the_run_log_is_opened_once_and_again_after_it_failed(store: MemoryStore, notifier: Notifier) -> None:
    opened: list[int] = []

    def open_store() -> MemoryStore:
        opened.append(1)
        return store

    loop = Scheduler(build_plan(), Launcher(store), open_store, notifier, clock=lambda: SUNDAY_NOON)  # type: ignore[arg-type]
    loop.tick()
    loop.tick()
    assert len(opened) == 1  # one connection for the life of the loop, not one a minute
    store.down = True
    loop.tick()
    store.down = False
    loop.tick()
    assert len(opened) == 2


def test_a_job_that_could_not_write_its_run_is_not_started_again_for_the_slot(
    store: MemoryStore, notifier: Notifier
) -> None:
    launcher = Launcher(store, silent=True)  # every run "loses" its row
    loop = scheduler(store, launcher, notifier, SUNDAY_NOON)
    loop.tick()
    started = len(launcher.started)
    loop.tick()
    assert len(launcher.started) == started


def test_without_the_run_log_nothing_starts_and_the_hook_fires_once(
    store: MemoryStore, notifier: Notifier
) -> None:
    launcher = Launcher(store)
    loop = scheduler(store, launcher, notifier, SUNDAY_NOON)
    store.down = True
    assert loop.tick() == {"scheduler": 1} and loop.tick() == {"scheduler": 1}
    assert launcher.started == [] and kinds(notifier) == [("scheduler_error", "scheduler")]
    assert "ConnectionError" in notifier.sent[0].summary
    store.down = False
    assert set(loop.tick().values()) == {0} and len(launcher.started) == 9
    store.down = True
    loop.tick()
    assert len(notifier.sent) == 2  # a new outage is a new alert


def test_run_sleeps_until_the_next_slot_in_short_steps(
    store: MemoryStore, notifier: Notifier, tmp_path: Path
) -> None:
    now = [SUNDAY_NOON]
    naps: list[float] = []

    class StopLoopError(Exception):
        pass

    def sleep(seconds: float) -> None:
        naps.append(seconds)
        if len(naps) == 3:
            raise StopLoopError
        now[0] += timedelta(seconds=seconds)

    heartbeat = tmp_path / "state" / "heartbeat"
    loop = Scheduler(
        build_plan(),
        Launcher(store),
        lambda: store,  # type: ignore[arg-type,return-value]
        notifier,
        clock=lambda: now[0],
        sleep=sleep,
        heartbeat=heartbeat,
    )
    with contextlib.suppress(StopLoopError):
        loop.run()
    assert naps == [60.0, 60.0, 60.0] and heartbeat.exists()
    assert Scheduler([], Launcher(store), lambda: store, notifier).run(once=True) == 1  # type: ignore[arg-type,return-value]
    beat(None)
