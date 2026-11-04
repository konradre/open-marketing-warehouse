"""Date windows: the rolling daily re-pull and the oldest-first backfill chunks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DAILY_WINDOW_DAYS = 10
"""The daily sync re-fetches this many days and replaces them (Search Console lags 2-3 days)."""

RETENTION_MONTHS = 16
"""Search Console keeps 16 months of data."""

CHUNK_DAYS = 90


@dataclass(frozen=True, order=True)
class Window:
    start: date
    end: date
    """Inclusive on both ends, as the API takes them."""

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"window ends before it starts: {self.start} > {self.end}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def __str__(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


def daily_window(today: date, days: int = DAILY_WINDOW_DAYS) -> Window:
    """The last ``days`` complete days, ending yesterday."""
    end = today - timedelta(days=1)
    return Window(end - timedelta(days=days - 1), end)


def retention_start(today: date, months: int = RETENTION_MONTHS) -> date:
    """The oldest day the API still serves: ``months`` calendar months back, day clamped to the month."""
    month_index = today.year * 12 + (today.month - 1) - months
    year, month = divmod(month_index, 12)
    month += 1
    last_day = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(today.day, last_day))


def backfill_chunks(
    today: date,
    completed_through: date | None,
    chunk_days: int = CHUNK_DAYS,
    daily_days: int = DAILY_WINDOW_DAYS,
    months: int = RETENTION_MONTHS,
) -> list[Window]:
    """Oldest-first chunks from the retention limit (or the day after the last completed chunk) up to the
    day before the daily window, which the daily sync owns."""
    start = retention_start(today, months)
    if completed_through is not None:
        start = max(start, completed_through + timedelta(days=1))
    stop = daily_window(today, daily_days).start - timedelta(days=1)
    chunks: list[Window] = []
    while start <= stop:
        end = min(start + timedelta(days=chunk_days - 1), stop)
        chunks.append(Window(start, end))
        start = end + timedelta(days=1)
    return chunks
