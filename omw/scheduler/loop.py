"""The scheduler loop (``omw schedule run``): start whatever is due, one job at a time.

A job is due when the latest slot of its schedule has not been attempted. The loop reads the attempts from
``ops.sync_runs``, so stopping and starting it neither repeats a finished slot nor forgets one that passed
while the host was down; a slot missed for days is caught up once, not once per day missed.

A job that failed (it raised, or its process died) is tried again for the same slot after a pause, a
bounded number of times: most failures of a pull are an API having a bad minute. The hook stays quiet
until the last attempt has failed too. A failed check is never retried.

Each job runs as its own ``omw sync <job>`` process: a crash or a leak in one sync cannot take the loop
down, and the job reports itself (run log, hook, exit code) exactly as it does under a systemd timer. The
loop only steps in when the process could not: it died, or it ran out of time.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from omw.pipelines.instants import instant
from omw.scheduler.jobs import Job, Planned
from omw.scheduler.notify import Alert, Notifier
from omw.scheduler.runs import HANDLED_EXITS, Outcome, RunStatus, RunStore, SlotState

log = logging.getLogger(__name__)

Launcher = Callable[[Job, datetime, int], int]
"""Runs one job for one slot and returns its exit code (``omw sync``'s). The third argument is how many
more attempts the loop will make if this one fails."""

MAX_SLEEP_SECONDS = 60.0
DEFAULT_TIMEOUT_MINUTES = 360
DEFAULT_RETRIES = 2
DEFAULT_RETRY_MINUTES = 30
EXIT_TIMED_OUT = 124
EXIT_NOT_STARTED = 127
CRASH_REASONS = {EXIT_TIMED_OUT: "ran out of time", EXIT_NOT_STARTED: "its process could not be started"}
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def heartbeat_path() -> Path:
    return Path(os.environ.get("OMW_HEARTBEAT") or Path(tempfile.gettempdir()) / "omw-scheduler.heartbeat")


def beat(path: Path | None) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def subprocess_launcher(
    extra_args: list[str], timeout_minutes: float = DEFAULT_TIMEOUT_MINUTES, heartbeat: Path | None = None
) -> Launcher:
    """Run each job as ``python -m omw.cli sync <job>``, killing it when it runs out of time."""

    def launch(job: Job, slot: datetime, retries_left: int = 0) -> int:
        command = [sys.executable, "-m", "omw.cli", "sync", job.name, "--slot", slot.isoformat()]
        command += ["--trigger", "schedule", "--retries-left", str(retries_left), *extra_args]
        deadline = time.monotonic() + timeout_minutes * 60
        process = subprocess.Popen(command)  # noqa: S603 - our own interpreter and module, no shell
        try:
            while True:
                try:
                    return process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    beat(heartbeat)
                    if time.monotonic() > deadline:
                        log.error("%s ran past %s minutes; stopping it", job.name, timeout_minutes)
                        process.kill()
                        process.wait()
                        return EXIT_TIMED_OUT
        finally:
            if process.poll() is None:  # the loop itself is being stopped
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()

    return launch


@dataclass
class Scheduler:
    plan: list[Planned]
    launch: Launcher
    store: Callable[[], RunStore]
    """Opens the run log. Called again after a failure, so a warehouse that was down is found again."""
    notifier: Notifier
    clock: Callable[[], datetime] = instant
    sleep: Callable[[float], None] = time.sleep
    heartbeat: Path | None = None
    retries: int = 0
    """Further attempts at a slot whose job failed or crashed. ``omw schedule run`` reads
    ``[scheduler] retries`` (2 unless set)."""
    retry_after: timedelta = timedelta(minutes=DEFAULT_RETRY_MINUTES)
    """The pause between a failed attempt and the next one."""
    started: dict[str, tuple[datetime, int]] = field(default_factory=dict)
    """job -> (slot, attempts this process started for it), kept beside the run log: a job that could not
    write its row (the warehouse went away) is still not started without bound for the same slot."""
    state_failed: bool = False
    once: bool = False
    _store: RunStore | None = None

    def _started_here(self, name: str, slot: datetime) -> int:
        mine = self.started.get(name)
        return mine[1] if mine is not None and mine[0] == slot else 0

    def due(self, now: datetime, states: dict[str, SlotState]) -> list[tuple[Planned, datetime, int]]:
        """(job, slot, attempts already made at that slot) for everything to start now."""
        found = []
        for planned in self.plan:
            name = planned.job.name
            slot = planned.schedule.latest(now)
            state = states.get(name)
            here = self._started_here(name, slot)
            if state is None or slot > state.slot:
                if here == 0:  # a new slot, not started by this process yet
                    found.append((planned, slot, 0))
            elif (
                slot == state.slot
                and not state.settled
                and max(state.attempts, here) <= self.retries
                and now >= state.finished_at + self.retry_after
            ):
                found.append((planned, slot, max(state.attempts, here)))
        return found

    def tick(self, now: datetime | None = None) -> dict[str, int]:
        """Run every due job once, in plan order. Returns job -> exit code."""
        now = now or self.clock()
        try:
            store = self._store = self._store or self.store()
            states = store.slot_states()
        except Exception as exc:  # noqa: BLE001 - no state, no runs; say so once and try again next tick
            self._store = None
            log.error("cannot read the run log: %s", exc)
            if not self.state_failed:
                self.state_failed = True
                self.notifier.send(
                    Alert(
                        "scheduler_error",
                        "error",
                        "scheduler",
                        f"cannot read ops.sync_runs, no job can start: {exc.__class__.__name__}: {exc}"[:500],
                    )
                )
            return {"scheduler": 1}
        self.state_failed = False
        due = self.due(now, states)
        covered = {name for planned, _, made in due for name in planned.job.covers if made == 0}
        codes: dict[str, int] = {}
        for planned, slot, made in due:
            job = planned.job
            self.started[job.name] = (slot, self._started_here(job.name, slot) + 1)
            # With --once nobody promises another tick, so every failure is reported as the last one.
            left = 0 if self.once else max(self.retries - made, 0)
            beat(self.heartbeat)
            if job.name in covered and made == 0:
                by = next(p.job.name for p, _, _ in due if job.name in p.job.covers)
                log.info("%s: slot %s is covered by %s, skipped", job.name, slot.isoformat(), by)
                self._record(store, job, RunStatus.SKIPPED, slot, f"covered by {by}")
                codes[job.name] = 0
                continue
            attempt = f" (attempt {made + 1} of {self.retries + 1})" if made else ""
            log.info("%s: starting for slot %s%s", job.name, slot.isoformat(), attempt)
            try:
                code = self.launch(job, slot, left)
            except Exception:  # noqa: BLE001 - a job that cannot even start must not take the loop down
                log.exception("%s could not be started", job.name)
                code = EXIT_NOT_STARTED
            codes[job.name] = code
            if code not in HANDLED_EXITS:
                what = CRASH_REASONS.get(code, f"its process ended with code {code}")
                self._record(store, job, RunStatus.CRASHED, slot, what)
                if left:
                    log.warning("%s %s; %d more attempt(s) to come", job.name, what, left)
                else:
                    self.notifier.send(Alert("job_crashed", "error", job.name, what, job.source))
        return codes

    def _record(self, store: RunStore, job: Job, status: RunStatus, slot: datetime, detail: str) -> None:
        at = self.clock()
        try:
            store.record(Outcome(job.name, job.source, status, slot, at, at, "schedule", detail=detail))
        except Exception:  # noqa: BLE001 - `attempted` still keeps the slot from being started again
            log.exception("could not write the run of %s to ops.sync_runs", job.name)

    def run(self, once: bool = False) -> int:
        """The loop. ``once`` runs what is due and returns non-zero when any of it failed."""
        if not self.plan:
            log.error("every job is off: nothing to schedule")
            return 1
        self.once = once
        while True:
            codes = self.tick()
            beat(self.heartbeat)
            if once:
                return 1 if any(codes.values()) else 0
            now = self.clock()
            wake = min(planned.schedule.next(now) for planned in self.plan)
            self.sleep(min(max((wake - now).total_seconds(), 1.0), MAX_SLEEP_SECONDS))
