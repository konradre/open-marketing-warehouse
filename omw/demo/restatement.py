"""What a daily sync did to the days already in the warehouse.

A platform revises recent days after the fact, so the daily sync re-fetches a rolling window and must
replace it, never append to it (DESIGN.md §4). ``observe`` reads one headline table per source before and
after the sync; ``compare`` says how many days came back with different numbers, how many are new, and how
many rows share a key with another row, which has to be none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omw.checks.results import Warehouse


@dataclass(frozen=True)
class Watch:
    source: str
    table: str
    keys: tuple[str, ...]
    """The columns that identify a row; no two rows may share them."""
    measure: str
    """An aggregate that moves when the platform restates a day."""
    label: str
    day: str = "date"
    """The expression a row is dated by."""


WATCHED: tuple[Watch, ...] = (
    Watch(
        "search_console",
        "raw.search_console___core",
        ("site_url", "search_type", "date"),
        "sum(clicks)",
        "clicks",
    ),
    Watch(
        "meta_ads",
        "raw.meta_ads___insights_campaigns",
        ("account_id", "date", "campaign_id"),
        "sum(spend)",
        "spend",
    ),
    Watch(
        "google_ads",
        "raw.google_ads___performance_campaigns",
        ("customer_id", "date", "campaign_id"),
        "sum(conversions)",
        "conversions",
    ),
    Watch("ga4", "raw.ga4___core", ("property_id", "date"), "sum(sessions)", "sessions"),
    Watch(
        "linkedin_ads",
        "raw.linkedin_ads___ad_analytics",
        ("account_id", "date", "campaign_id"),
        "sum(cost)",
        "cost",
    ),
    Watch(
        "stripe",
        "raw.stripe___invoices",
        ("id",),
        "countIf(status = 'paid')",
        "paid invoices",
        "toDate(created)",
    ),
    Watch(
        "twenty",
        "raw.twenty___opportunities",
        ("id",),
        "countIf(stage != 'NEW')",
        "opportunities past NEW",
        "toDate(created_at)",
    ),
)


@dataclass(frozen=True)
class Observation:
    rows: int
    duplicates: int
    by_day: dict[str, float]


@dataclass(frozen=True)
class Change:
    watch: Watch
    before: Observation
    after: Observation

    @property
    def restated(self) -> list[str]:
        """Days that were loaded before and came back with a different number."""
        return sorted(
            day
            for day, value in self.after.by_day.items()
            if day in self.before.by_day and value != self.before.by_day[day]
        )

    @property
    def new(self) -> list[str]:
        return sorted(set(self.after.by_day) - set(self.before.by_day))

    @property
    def example(self) -> str:
        """The latest restated day with its number before and after."""
        if not self.restated:
            return ""
        day = self.restated[-1]
        return f"{day}: {self.before.by_day[day]:,.2f} -> {self.after.by_day[day]:,.2f} {self.watch.label}"


def observe(client: Warehouse, watch: Watch) -> Observation:
    keys = ", ".join(watch.keys)
    rows, distinct = client.query(f"SELECT count(), uniqExact({keys}) FROM {watch.table}").result_rows[0]
    by_day = client.query(
        f"SELECT toString({watch.day}) AS day, toFloat64({watch.measure}) FROM {watch.table} GROUP BY day"
    ).result_rows
    return Observation(
        int(rows), int(rows) - int(distinct), {str(day): float(value) for day, value in by_day}
    )


def observe_all(client: Warehouse) -> dict[str, Observation]:
    return {watch.source: observe(client, watch) for watch in WATCHED}


def compare(before: dict[str, Observation], after: dict[str, Observation]) -> list[Change]:
    return [Change(watch, before[watch.source], after[watch.source]) for watch in WATCHED]


def as_rows(changes: list[Change]) -> tuple[list[str], list[list[Any]]]:
    """The comparison as a table: one line per source."""
    columns = [
        "source",
        "rows before",
        "rows after",
        "days restated",
        "new days",
        "duplicate keys",
        "for example",
    ]
    rows = [
        [
            change.watch.source,
            change.before.rows,
            change.after.rows,
            len(change.restated),
            len(change.new),
            change.after.duplicates,
            change.example,
        ]
        for change in changes
    ]
    return columns, rows
