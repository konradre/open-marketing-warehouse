"""One job, the way every trigger runs it (``omw sync <job>``).

The pipeline first, then the post-sync checks of its source whether or not it loaded (a failed sync must
still move the freshness verdict), a row in ``ops.sync_runs``, the hook when anything failed or is about
to expire, and an exit code the caller can act on: 0, 1 (the job failed) or 3 (it ran, a check failed).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import dlt

from omw import backup
from omw.checks.expiry import CHECK as EXPIRY_CHECK
from omw.checks.expiry import read_warn_days
from omw.checks.results import CheckResult
from omw.checks.runner import CheckRun, apply_ops_sql, run_checks
from omw.pipelines.instants import instant
from omw.scheduler.jobs import Job, checked_sources
from omw.scheduler.modes import Mode
from omw.scheduler.notify import Alert, Notifier
from omw.scheduler.runs import Outcome, RunStatus, RunStore

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]
DEFAULT_KEEP = 7


def describe(result: CheckResult) -> str:
    return f"{result.status.upper()} {result.source}/{result.check}/{result.report}: {result.detail}"


def backup_dir() -> Path | None:
    """Where this process sees the backup files (``OMW_BACKUP_DIR``), when it sees them at all."""
    configured = os.environ.get("OMW_BACKUP_DIR", "").strip()
    return Path(configured) if configured else None


def backup_keep() -> int:
    return int(dlt.config.get("backup.keep", int) or DEFAULT_KEEP)


def _run_backup(job: Job, client: Any) -> tuple[int, str]:
    if job.kind == "backup":
        made = backup.create(client)
        directory = backup_dir()
        detail = f"{made.name}: {made.tables} tables, {made.rows:,} rows, {made.bytes:,} bytes"
        if directory is not None and directory.is_dir():
            removed = backup.prune(directory, backup_keep(), client)
            if removed:
                detail += f"; pruned {', '.join(removed)}"
        return made.rows, detail
    drill = backup.verify(client)
    if not drill.ok:
        raise backup.BackupError(f"the restore drill of {drill.name} failed: {'; '.join(drill.problems)}")
    return (
        drill.rows,
        f"{drill.name} restored into scratch databases: {drill.tables} tables, {drill.rows:,} rows match",
    )


def _checks(job: Job, mode: Mode, client: Any) -> CheckRun:
    apply_ops_sql(client)
    return run_checks(
        client,
        budgets=mode.budgets(),
        sources={job.source},
        today=mode.today(),
        expiries=mode.expiries(job.source),
        expiry_warn_days=read_warn_days(),
    )


VERDICTS = "ops.check_verdicts"


def failing_verdicts(client: Any) -> set[tuple[str, str, str]]:
    """(source, check, report) of every check whose latest verdict is ``fail``."""
    rows = client.query(f"SELECT source, check, report FROM {VERDICTS} WHERE status = 'fail'").result_rows
    return {(str(source), str(check), str(report)) for source, check, report in rows}


def _sweep(mode: Mode, client: Any) -> tuple[CheckRun, list[CheckResult]]:
    """Every check of every scheduled source. Returns the run and the checks that were not failing before
    it: a source going stale between its own syncs is news once, not once an hour."""
    apply_ops_sql(client)
    before = failing_verdicts(client)
    budgets, sources = checked_sources(mode.budgets())
    expiries: dict[str, Any] = {}
    for source in sorted(sources):
        expiries.update(mode.expiries(source))
    run = run_checks(
        client,
        budgets=budgets,
        sources=sources,
        today=mode.today(),
        expiries=expiries,
        expiry_warn_days=read_warn_days(),
    )
    return run, [r for r in run.failed if (r.source, r.check, r.report) not in before]


def run_job(
    job: Job,
    mode: Mode,
    notifier: Notifier,
    slot: datetime | None = None,
    trigger: str = "manual",
    clock: Clock = instant,
    retries_left: int = 0,
) -> Outcome:
    """Run ``job`` once and report it everywhere it has to be reported. Never raises for a failed job.

    ``retries_left`` is the scheduler loop saying it will try this slot again: a job that failed is then
    logged and recorded, and the hook waits for the last attempt.
    """
    started = clock()
    outcome = Outcome(job.name, job.source, RunStatus.OK, slot or started, started, started, trigger)
    client: Any = None
    try:
        client = mode.client()
        if job.kind == "sync":
            counts = mode.sync(job)
            outcome.rows = sum(count for table, count in counts.items() if not table.startswith("_dlt"))
            outcome.detail = f"{outcome.rows:,} rows"
        elif job.kind == "checks":
            swept, news = _sweep(mode, client)
            outcome.rows, outcome.detail = len(swept.results), swept.summary()
            outcome.checks_failed, outcome.checks_warned = len(swept.failed), len(swept.warned)
            if swept.failed:
                outcome.status = RunStatus.CHECK_FAILED
                outcome.detail += ": " + "; ".join(describe(result) for result in swept.failed)
            if news:
                summary = f"{len(news)} check(s) started failing between syncs"
                notifier.send(
                    Alert("check_failed", "error", job.name, summary, "", tuple(describe(r) for r in news))
                )
        else:
            outcome.rows, outcome.detail = _run_backup(job, client)
        log.info("%s: %s", job.name, outcome.detail)
    except Exception as exc:  # noqa: BLE001 - any failure is this job's failure, reported below
        log.exception("%s failed", job.name)
        outcome.status = RunStatus.FAILED
        outcome.problems.append(backup.brief(exc, 600))

    checks: CheckRun | None = None
    if job.kind == "sync" and client is not None:
        try:
            checks = _checks(job, mode, client)
        except Exception as exc:  # noqa: BLE001 - checks that cannot run are a failed check
            log.exception("the post-sync checks of %s could not run", job.source)
            outcome.problems.append(f"the post-sync checks could not run: {backup.brief(exc)}")
            if outcome.status is RunStatus.OK:
                outcome.status = RunStatus.CHECK_FAILED
    if checks is not None:
        for result in checks.results:
            if result.status != "pass":
                log.warning("%s", describe(result))
        log.info("%s checks: %s", job.source, checks.summary())
        outcome.checks_failed, outcome.checks_warned = len(checks.failed), len(checks.warned)
        outcome.problems += [describe(result) for result in checks.failed]
        if checks.failed and outcome.status is RunStatus.OK:
            outcome.status = RunStatus.CHECK_FAILED

    outcome.finished_at = clock()
    if outcome.problems:
        outcome.detail = "; ".join(outcome.problems)
    if client is not None:
        try:
            RunStore(client).record(outcome)
        except Exception:  # noqa: BLE001 - the run still ends with its own exit code and alert
            log.exception("could not write the run of %s to ops.sync_runs", job.name)
    _alert(job, outcome, checks, notifier, retries_left)
    return outcome


def _alert(
    job: Job, outcome: Outcome, checks: CheckRun | None, notifier: Notifier, retries_left: int = 0
) -> None:
    if outcome.status is RunStatus.FAILED and retries_left > 0:
        log.warning("%s failed; the scheduler will try again (%d more attempt(s))", job.name, retries_left)
    elif outcome.status is RunStatus.FAILED:
        kind = {"sync": "sync_failed", "checks": "check_failed"}.get(job.kind, "backup_failed")
        notifier.send(
            Alert(kind, "error", job.name, outcome.problems[0], job.source, tuple(outcome.problems[1:]))
        )
    elif outcome.status is RunStatus.CHECK_FAILED and job.kind != "checks":  # the sweep alerts on news only
        summary = f"{len(outcome.problems)} post-sync check(s) failed after the sync"
        notifier.send(Alert("check_failed", "error", job.name, summary, job.source, tuple(outcome.problems)))
    expiring = [r for r in (checks.warned if checks else []) if r.check == EXPIRY_CHECK]
    if expiring:
        notifier.send(
            Alert(
                "expiry_warning",
                "warning",
                job.name,
                expiring[0].detail,
                job.source,
                tuple(describe(result) for result in expiring[1:]),
            )
        )
