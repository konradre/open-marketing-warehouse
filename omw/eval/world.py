"""The synthetic world the evaluation runs on: a fixed seed, a fixed calendar and two views of the same data.

``tables`` holds the rows as the agent sees them, keyed by ``marts`` view name: this is what the ground truth
is computed from, in plain Python. ``raw`` holds the rows as a dlt pipeline would have loaded them, keyed by
``raw`` table name: this is what gets inserted, and the repo's own ``sql/*_marts*.sql`` views turn it back
into ``tables``. The two are generated separately on purpose, so a view that disagrees with the truth shows.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

SEED = 20260601
"""The one seed. Every number is a pure function of it, an entity and a day (``generate.rng``)."""
START = date(2026, 6, 1)
END = date(2026, 9, 30)
TODAY = date(2026, 10, 1)
"""The day the evaluation pretends it is: named ranges and restatement windows resolve against it."""
NOW = datetime(2026, 10, 1, 6, 0)
"""The wall clock the post-sync checks are judged against when the seed runs them."""

Row = dict[str, Any]


@dataclass(frozen=True)
class Period:
    """An inclusive range of days."""

    start: date
    end: date

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.start <= day <= self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Period:
        """The same number of days immediately before."""
        end = self.start - timedelta(days=1)
        return Period(end - timedelta(days=self.days - 1), end)

    def iso(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    def sql(self, column: str = "date") -> str:
        return f"{column} BETWEEN '{self.start}' AND '{self.end}'"


JUN = Period(date(2026, 6, 1), date(2026, 6, 30))
JUL = Period(date(2026, 7, 1), date(2026, 7, 31))
AUG = Period(date(2026, 8, 1), date(2026, 8, 31))
SEP = Period(date(2026, 9, 1), date(2026, 9, 30))
Q3 = Period(date(2026, 7, 1), date(2026, 9, 30))
LAST_7 = Period(date(2026, 9, 24), date(2026, 9, 30))
"""``last_7_days`` on ``TODAY``; ``last_30_days`` and ``last_month`` are both ``SEP``."""
YESTERDAY = Period(date(2026, 9, 30), date(2026, 9, 30))


def days(period: Period | None = None) -> list[date]:
    period = period or Period(START, END)
    return [period.start + timedelta(days=n) for n in range(period.days)]


Predicate = Callable[[Any], bool]


def _matches(value: Any, wanted: Any) -> bool:
    if isinstance(wanted, Period):
        return value in wanted
    if isinstance(wanted, set | frozenset | tuple | list):
        return value in wanted
    if callable(wanted):
        return bool(wanted(value))
    return bool(value == wanted)


@dataclass
class World:
    seed: int
    tables: dict[str, list[Row]] = field(default_factory=dict)
    """Rows as the ``marts`` views expose them, keyed by view name without the database."""
    raw: dict[str, list[Row]] = field(default_factory=dict)
    """Rows as dlt loads them, keyed by ``raw`` table name without the database."""
    campaign_keys: dict[tuple[str, str], str] = field(default_factory=dict)
    """(source, native campaign id) -> cross-source campaign key."""
    manual_keys: list[Row] = field(default_factory=list)
    """Mapping rows set by hand (``origin = manual``); the rest are derived from the campaign names."""

    def rows(self, table: str, **where: Any) -> list[Row]:
        """Rows of one ``marts`` view matching every filter: a value, a collection, a ``Period`` or a
        predicate."""
        found = self.tables[table]
        for column, wanted in where.items():
            found = [row for row in found if _matches(row[column], wanted)]
        return found

    def add(self, table: str, rows: Iterable[Row]) -> None:
        self.tables.setdefault(table, []).extend(rows)

    def add_raw(self, table: str, rows: Iterable[Row]) -> None:
        self.raw.setdefault(table, []).extend(rows)

    def key_of(self, source: str, campaign_id: str) -> str:
        return self.campaign_keys.get((source, campaign_id), f"{source}:{campaign_id}")
