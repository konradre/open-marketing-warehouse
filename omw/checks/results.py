"""One row per check outcome, written to ``ops.check_results``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class Warehouse(Protocol):
    """The slice of clickhouse-connect the checks use, so tests can fake it."""

    def query(self, sql: str) -> Any: ...

    def command(self, sql: str) -> Any: ...

    def insert(self, table: str, data: list[list[Any]], column_names: list[str]) -> Any: ...


CHECK_RESULTS = "ops.check_results"
CHECK_RESULT_COLUMNS = (
    "checked_at",
    "source",
    "check",
    "report",
    "status",
    "observed",
    "expected",
    "detail",
)


@dataclass(frozen=True)
class CheckResult:
    source: str
    check: str
    report: str
    status: Status
    observed: float
    expected: float
    detail: str
    checked_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC).replace(tzinfo=None))

    @property
    def failed(self) -> bool:
        return self.status is Status.FAIL

    def row(self) -> list[Any]:
        return [
            self.checked_at,
            self.source,
            self.check,
            self.report,
            str(self.status),
            self.observed,
            self.expected,
            self.detail,
        ]


def write_results(client: Warehouse, results: list[CheckResult]) -> int:
    if not results:
        return 0
    client.insert(CHECK_RESULTS, [r.row() for r in results], column_names=list(CHECK_RESULT_COLUMNS))
    return len(results)
