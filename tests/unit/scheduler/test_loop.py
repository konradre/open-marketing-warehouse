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
        self.left: list[tuple[str, int]] = []
        self.now: datetime | None = None

    def __call__(self, job: Job, slot: datetime, retries_left: int = 0) -> int:
        self.started.append((job.name, slot))
        self.left.append((job.name, retries_left))
        code = self.codes.get(job.name, 0)
        status = {0: RunStatus.OK, EXIT_FAILED: RunStatus.FAILED, EXIT_CHECK_FAILED: RunStatus.CHECK_FAILED}
        if code in status and not self.silent:
            at = self.now or slot
            self.store.record(Outcome(job.name, job.source, status[code], slot, at, at, "schedule"))
        return code


DAILY = {"checks": "off"}
"""The plan most tests use: the daily and weekly jobs, without the hourly one (it has its own tests)."""


def scheduler(store: MemoryStore, launcher: Launcher, notifier: Notifier, now: datetime) -> Scheduler:
    return Scheduler(build_plan(DAILY), launcher, lambda: store, notifier, clock=lambda: now)  # type: ignore[arg-type,return-value]


def test_the_hourly_checks_run_last_then_once_an_hour(store: MemoryStore, notifier: Notifier) -> None:
    launcher = Launcher(store)

    def at(now: datetime) -> Scheduler:
        return Scheduler(build_plan(), launcher, lambda: store, notifier, clock=lambda: now)  # type: ignore[arg-type,return-value]

    first = at(SUNDAY_NOON).tick()
    assert list(first)[-1] == "checks" and len(first) == 11  # after every sync of the first tick
    assert dict(launcher.started)["checks"] == datetime(2026, 11, 22, 11, 50, tzinfo=UTC)
    assert at(SUNDAY_NOON + timedelta(minutes=49)).tick() == {}
    assert at(SUNDAY_NOON + timedelta(minutes=50)).tick() == {"checks": 0}
    assert at(SUNDAY_NOON + timedelta(minutes=55)).tick() == {}
    launcher.started.clear()
    assert at(SUNDAY_NOON + timedelta(hours=7)).tick() == {"checks": 0}  # hours of downtime: one run
    assert launcher.started == [("checks", datetime(2026, 11, 22, 18, 50, tzinfo=UTC))]


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
        def __call__(self, job: Job, slot: datetime, retries_left: int = 0) -> int:
            if job.name == "ga4":
                raise OSError("cannot allocate memory")
            return super().__call__(job, slot, retries_left)

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

    loop = Scheduler(build_plan(DAILY), Launcher(store), open_store, notifier, clock=lambda: SUNDAY_NOON)  # type: ignore[arg-type]
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


def retrying(store: MemoryStore, launcher: Launcher, notifier: Notifier, clock: list[datetime]) -> Scheduler:
    def now() -> datetime:
        launcher.now = clock[0]
        return clock[0]

    return Scheduler(
        build_plan({"backup": "off", "backup_verify": "off", "checks": "off"}),
        launcher,
        lambda: store,  # type: ignore[arg-type,return-value]
        notifier,
        clock=now,
        retries=2,
        retry_after=timedelta(minutes=30),
    )


def test_a_failed_job_is_tried_again_after_the_pause_and_only_that_job(
    store: MemoryStore, notifier: Notifier
) -> None:
    clock = [SUNDAY_NOON]
    launcher = Launcher(store, {"ga4": EXIT_FAILED})
    loop = retrying(store, launcher, notifier, clock)
    assert loop.tick()["ga4"] == 1
    assert ("ga4", 2) in launcher.left  # the job is told two more attempts will follow
    launcher.started.clear()
    clock[0] += timedelta(minutes=29)
    assert loop.tick() == {}  # not yet
    clock[0] += timedelta(minutes=1)
    launcher.codes = {}  # the API is back
    assert loop.tick() == {"ga4": 0}
    assert launcher.started == [("ga4", datetime(2026, 11, 22, 4, 15, tzinfo=UTC))]  # the same slot
    clock[0] += timedelta(hours=3)
    assert loop.tick() == {}  # it succeeded: nothing left to retry
    assert [status for job, status in store.statuses() if job == "ga4"] == ["failed", "ok"]


def test_a_retry_that_succeeded_in_the_same_second_as_the_failure_settles_the_slot(
    store: MemoryStore, notifier: Notifier
) -> None:
    """Seen in CI: both attempts carried the same second, so "the latest attempt" had no answer and the
    slot was run a third time. Any attempt that did not fail settles it."""
    clock = [SUNDAY_NOON]
    launcher = Launcher(store, {"ga4": EXIT_FAILED})
    loop = retrying(store, launcher, notifier, clock)
    loop.retry_after = timedelta(0)
    loop.tick()
    launcher.codes = {}
    assert loop.tick() == {"ga4": 0}  # no time has passed on the clock
    assert [o.finished_at for o in store.outcomes if o.job == "ga4"] == [SUNDAY_NOON, SUNDAY_NOON]
    store.outcomes.reverse()  # whichever order the log returns them in
    assert loop.tick() == {} and retrying(store, launcher, notifier, clock).tick() == {}


def test_retries_are_bounded_and_the_last_attempt_is_told_so(store: MemoryStore, notifier: Notifier) -> None:
    clock = [SUNDAY_NOON]
    launcher = Launcher(store, {"stripe": EXIT_FAILED})
    loop = retrying(store, launcher, notifier, clock)
    for _ in range(6):
        loop.tick()
        clock[0] += timedelta(minutes=31)
    assert [left for job, left in launcher.left if job == "stripe"] == [2, 1, 0]
    assert [status for job, status in store.statuses() if job == "stripe"] == ["failed"] * 3
    # A restarted loop reads the three attempts from the run log and does not start a fourth.
    fresh = retrying(store, launcher, notifier, clock)
    assert fresh.tick() == {}


def test_a_failed_check_is_not_retried(store: MemoryStore, notifier: Notifier) -> None:
    clock = [SUNDAY_NOON]
    launcher = Launcher(store, {"ga4": EXIT_CHECK_FAILED})
    loop = retrying(store, launcher, notifier, clock)
    assert loop.tick()["ga4"] == 3
    clock[0] += timedelta(hours=2)
    assert loop.tick() == {}


def test_a_crash_is_retried_and_reported_only_when_the_last_attempt_crashed_too(
    store: MemoryStore, notifier: Notifier
) -> None:
    clock = [SUNDAY_NOON]
    launcher = Launcher(store, {"twenty": -9})
    loop = retrying(store, launcher, notifier, clock)
    for _ in range(4):
        loop.tick()
        clock[0] += timedelta(minutes=31)
    assert [status for job, status in store.statuses() if job == "twenty"] == ["crashed"] * 3
    assert kinds(notifier) == [("job_crashed", "twenty")]  # once, after the third


def test_a_job_whose_runs_never_reach_the_log_is_still_bounded(
    store: MemoryStore, notifier: Notifier
) -> None:
    clock = [SUNDAY_NOON]
    launcher = Launcher(store, {"ga4": EXIT_FAILED}, silent=True)  # failed, and could not say so
    loop = retrying(store, launcher, notifier, clock)
    for _ in range(8):
        loop.tick()
        clock[0] += timedelta(minutes=31)
    assert len([name for name, _ in launcher.started if name == "ga4"]) == 1


def test_with_once_every_failure_is_the_last_one(store: MemoryStore, notifier: Notifier) -> None:
    clock = [SUNDAY_NOON]
    launcher = Launcher(store, {"ga4": EXIT_FAILED})
    loop = retrying(store, launcher, notifier, clock)
    assert loop.run(once=True) == 1
    assert ("ga4", 0) in launcher.left  # nobody promises another tick, so the job fires the hook itself
    clock[0] += timedelta(minutes=31)
    launcher.left.clear()
    assert retrying(store, launcher, notifier, clock).run(once=True) == 1  # the next invocation retries it
    assert launcher.left == [("ga4", 0)]


def test_the_weekly_reconcile_covers_the_daily_once_not_on_its_retries(
    store: MemoryStore, notifier: Notifier
) -> None:
    clock = [SUNDAY_NOON]
    launcher = Launcher(store, {"meta_ads_weekly": EXIT_FAILED})
    loop = retrying(store, launcher, notifier, clock)
    loop.tick()
    clock[0] += timedelta(minutes=31)
    assert loop.tick() == {"meta_ads_weekly": 1}
    assert [status for job, status in store.statuses() if job == "meta_ads_daily"] == ["skipped"]
