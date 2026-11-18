"""``ops.sync_runs``: one row per attempt of a job, and what the scheduler loop reads its state from."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from omw.checks.results import Warehouse
from omw.checks.runner import apply_ops_sql
from omw.pipelines.instants import instant
from omw.pipelines.marts import SQL_DIR

RUNS_SQL = SQL_DIR / "75_ops_scheduler.sql"
SYNC_RUNS = "ops.sync_runs"
RUN_COLUMNS = (
    "started_at",
    "finished_at",
    "job",
    "source",
    "slot",
    "trigger",
    "status",
    "rows",
    "checks_failed",
    "checks_warned",
    "detail",
)
DETAIL_LIMIT = 2000

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CHECK_FAILED = 3
HANDLED_EXITS = (EXIT_OK, EXIT_FAILED, EXIT_CHECK_FAILED)
"""Exit codes of ``omw sync`` after it recorded the run and fired the hook itself. Any other code means the
process died before it could (killed, out of memory, a usage error)."""


class RunStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    CHECK_FAILED = "check_failed"
    CRASHED = "crashed"
    SKIPPED = "skipped"


@dataclass
class Outcome:
    """What one attempt of a job came to."""

    job: str
    source: str
    status: RunStatus
    slot: datetime
    started_at: datetime
    finished_at: datetime
    trigger: str = "manual"
    rows: int = 0
    checks_failed: int = 0
    checks_warned: int = 0
    detail: str = ""
    problems: list[str] = field(default_factory=list)
    """One line per thing that went wrong, for the alert."""

    @property
    def exit_code(self) -> int:
        if self.status in (RunStatus.OK, RunStatus.SKIPPED):
            return EXIT_OK
        return EXIT_CHECK_FAILED if self.status is RunStatus.CHECK_FAILED else EXIT_FAILED

    def row(self) -> list[Any]:
        return [
            instant(self.started_at),
            instant(self.finished_at),
            self.job,
            self.source,
            instant(self.slot),
            self.trigger,
            str(self.status),
            max(int(self.rows), 0),
            self.checks_failed,
            self.checks_warned,
            self.detail[:DETAIL_LIMIT],
        ]


RETRYABLE = (RunStatus.FAILED, RunStatus.CRASHED)
"""Outcomes a second attempt can change. A failed check is not one: the data loaded, and loading it again
gives the same verdict."""


@dataclass(frozen=True)
class SlotState:
    """Where a job stands on the latest slot it was run for."""

    slot: datetime
    attempts: int
    settled: bool
    """Whether any attempt ended in something a retry cannot improve (``ok``, ``check_failed``, ``skipped``).
    Asked this way, and not "how did the latest attempt end", because two attempts can carry the same
    second and then have no order."""
    finished_at: datetime
    """When the latest attempt ended."""


@dataclass(frozen=True)
class LastRun:
    status: str
    started_at: datetime
    slot: datetime
    detail: str


def _utc(seconds: int) -> datetime:
    return datetime.fromtimestamp(int(seconds), tz=UTC)


class RunStore:
    """The run log in the warehouse, through the pipeline user's client."""

    def __init__(self, client: Warehouse) -> None:
        self.client = client
        self._ready = False

    def ensure(self) -> None:
        if not self._ready:
            apply_ops_sql(self.client, RUNS_SQL)
            self._ready = True

    def record(self, outcome: Outcome) -> None:
        self.ensure()
        self.client.insert(SYNC_RUNS, [outcome.row()], column_names=list(RUN_COLUMNS))

    def slot_states(self) -> dict[str, SlotState]:
        """job -> the latest slot any run of it answered, with how often it was tried and how that ended."""
        self.ensure()
        retryable = ", ".join(f"'{status}'" for status in RETRYABLE)
        rows = self.client.query(
            f"SELECT job, toUnixTimestamp(slot), count(), countIf(status NOT IN ({retryable})), "
            f"toUnixTimestamp(max(finished_at)) FROM {SYNC_RUNS} "
            f"WHERE (job, slot) IN (SELECT job, max(slot) FROM {SYNC_RUNS} GROUP BY job) GROUP BY job, slot"
        ).result_rows
        return {
            str(job): SlotState(_utc(slot), int(attempts), bool(settled), _utc(finished))
            for job, slot, attempts, settled, finished in rows
        }

    def last_runs(self) -> dict[str, LastRun]:
        """job -> its most recent attempt."""
        self.ensure()
        rows = self.client.query(
            f"SELECT job, argMax(status, started_at), toUnixTimestamp(max(started_at)), "
            f"toUnixTimestamp(argMax(slot, started_at)), argMax(detail, started_at) "
            f"FROM {SYNC_RUNS} GROUP BY job"
        ).result_rows
        return {
            str(job): LastRun(str(status), _utc(started), _utc(slot), str(detail))
            for job, status, started, slot, detail in rows
        }
