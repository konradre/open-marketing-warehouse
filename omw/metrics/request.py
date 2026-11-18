"""What the agent asks for: metrics, dimensions, filters, a time range and an optional comparison."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

Compare = Literal["previous_period", "week", "month", "year"]
Relative = Literal[
    "today",
    "yesterday",
    "last_7_days",
    "last_30_days",
    "last_90_days",
    "month_to_date",
    "last_month",
    "year_to_date",
]
"""Named time ranges resolved against the server's ``today``. ``last_N_days`` ends yesterday."""
RELATIVE_RANGES: tuple[str, ...] = get_args(Relative)

FilterValue = str | int | float | bool | list[str] | list[int]


class TimeRange(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> TimeRange:
        if self.end < self.start:
            raise ValueError(f"time_range ends before it starts: {self.start} > {self.end}")
        return self

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def whole_months(self) -> bool:
        """True when the range runs from the first day of a month to the last day of a month."""
        return self.start.day == 1 and self.end == _month_end(self.end)

    def overlaps(self, other: TimeRange) -> int:
        """Days the two ranges share."""
        return max(0, (min(self.end, other.end) - max(self.start, other.start)).days + 1)

    def shifted(self, compare: Compare) -> TimeRange:
        """The comparison period.

        ``previous_period``: the same number of days immediately before. ``week``: the same weekdays seven
        days earlier. ``month`` and ``year``: the calendar period before. Whole calendar months compare
        with whole calendar months (September with 1 to 31 August, February 2025 with all 29 days of
        February 2024); any other range moves each end to the same day of the earlier month, held at that
        month's last day when it is shorter (31 March becomes 28 February).
        """
        if compare == "previous_period":
            end = self.start - timedelta(days=1)
            return TimeRange(start=end - timedelta(days=self.days - 1), end=end)
        if compare == "week":
            return TimeRange(start=self.start - timedelta(days=7), end=self.end - timedelta(days=7))
        months = 1 if compare == "month" else 12
        start = _months_back(self.start, months)
        if self.whole_months:
            return TimeRange(start=start, end=_month_end(_months_back(self.end.replace(day=1), months)))
        return TimeRange(start=start, end=_months_back(self.end, months))


def _month_end(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1) - timedelta(days=1)


def _months_back(day: date, months: int) -> date:
    """The same day ``months`` earlier, or that month's last day when it has no such day."""
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    first = date(year, month + 1, 1)
    return first.replace(day=min(day.day, _month_end(first).day))


class Window(BaseModel):
    """A resolved range of days, as stated in an answer."""

    start: date
    end: date
    days: int

    @classmethod
    def of(cls, time_range: TimeRange) -> Window:
        return cls(start=time_range.start, end=time_range.end, days=time_range.days)


class Comparison(BaseModel):
    """What a ``compare`` request compared: the ``current`` rows cover one window, ``previous`` the other."""

    compare: Compare
    current: Window
    previous: Window
    overlap_days: int = 0
    """Days counted in both windows (``week`` on a range longer than seven days)."""

    @classmethod
    def between(cls, compare: Compare, current: TimeRange, previous: TimeRange) -> Comparison:
        return cls(
            compare=compare,
            current=Window.of(current),
            previous=Window.of(previous),
            overlap_days=current.overlaps(previous),
        )


def relative_range(name: Relative, today: date) -> TimeRange:
    """The dates a named range covers on ``today``. ``last_N_days`` are the N full days before today."""
    yesterday = today - timedelta(days=1)
    if name == "today":
        return TimeRange(start=today, end=today)
    if name == "yesterday":
        return TimeRange(start=yesterday, end=yesterday)
    if name == "month_to_date":
        return TimeRange(start=today.replace(day=1), end=today)
    if name == "year_to_date":
        return TimeRange(start=today.replace(month=1, day=1), end=today)
    if name == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return TimeRange(start=end.replace(day=1), end=end)
    days = int(name.split("_")[1])
    return TimeRange(start=today - timedelta(days=days), end=yesterday)


class MetricRequest(BaseModel):
    metrics: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    filters: dict[str, FilterValue] = Field(default_factory=dict)
    time_range: TimeRange | Relative | None = None
    """Explicit ``{start, end}`` dates, or a named range resolved against the server's ``today``. Needed by
    every dated measure; a snapshot measure (a campaign list, the subscription list) has no dates."""
    compare: Compare | None = None
    limit: int = Field(default=1000, ge=1, le=10_000)

    def window(self, today: date) -> TimeRange | None:
        """The explicit dates of the request's time range, when it has one."""
        if self.time_range is None or isinstance(self.time_range, TimeRange):
            return self.time_range
        return relative_range(self.time_range, today)

    @field_validator("metrics", "dimensions")
    @classmethod
    def _no_duplicates(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError(f"duplicate names in {values}")
        return values
