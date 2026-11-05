"""Run every check against the warehouse, write the results, summarise."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from omw.checks.freshness import freshness_checks
from omw.checks.overcount import overcount_checks
from omw.checks.results import CheckResult, Status, Warehouse, write_results
from omw.checks.rowcount import rowcount_checks
from omw.pipelines.marts import SQL_DIR, statements

OPS_SQL = SQL_DIR / "70_ops_checks.sql"


@dataclass
class CheckRun:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.FAIL]

    @property
    def warned(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.WARN]

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        counts = {s: sum(1 for r in self.results if r.status is s) for s in Status}
        return ", ".join(f"{counts[s]} {s}" for s in Status)


def existing_objects(client: Warehouse) -> set[str]:
    """``database.name`` of every table and view the client can see in raw, marts and ops."""
    rows = client.query(
        "SELECT database, name FROM system.tables WHERE database IN ('raw', 'marts', 'ops')"
    ).result_rows
    return {f"{database}.{name}" for database, name in rows}


def apply_ops_sql(client: Warehouse, sql_file: Path = OPS_SQL) -> int:
    stmts = statements(sql_file.read_text())
    for statement in stmts:
        client.command(statement)
    return len(stmts)


def run_checks(
    client: Warehouse,
    now: datetime | None = None,
    budgets: dict[str, float] | None = None,
    sources: set[str] | None = None,
    write: bool = True,
    today: date | None = None,
) -> CheckRun:
    """``now`` is the wall clock freshness is judged against; ``today`` (default: its date) anchors the
    over-count and row-count windows, so tests can check fixture days loaded at another time."""
    now = now or datetime.now(tz=UTC).replace(tzinfo=None)
    today = today or now.date()
    existing = existing_objects(client)
    run = CheckRun()
    run.results += freshness_checks(client, now, budgets, existing)
    run.results += overcount_checks(client, today, existing_views=existing)
    run.results += rowcount_checks(client, today, existing_views=existing)
    if sources:
        run.results = [r for r in run.results if r.source in sources]
    if write:
        write_results(client, run.results)
    return run
