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

    def shifted(self, compare: Compare) -> TimeRange:
        """The comparison period: the same length immediately before, or a week/month/year earlier."""
        if compare == "previous_period":
            end = self.start - timedelta(days=1)
            return TimeRange(start=end - timedelta(days=self.days - 1), end=end)
        if compare == "week":
            return TimeRange(start=self.start - timedelta(days=7), end=self.end - timedelta(days=7))
        if compare == "month":
            return TimeRange(start=_months_back(self.start, 1), end=_months_back(self.end, 1))
        return TimeRange(start=_months_back(self.start, 12), end=_months_back(self.end, 12))


def _months_back(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


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
    time_range: TimeRange | Relative
    """Explicit ``{start, end}`` dates, or a named range resolved against the server's ``today``."""
    compare: Compare | None = None
    limit: int = Field(default=1000, ge=1, le=10_000)

    def window(self, today: date) -> TimeRange:
        """The explicit dates of the request's time range."""
        if isinstance(self.time_range, TimeRange):
            return self.time_range
        return relative_range(self.time_range, today)

    @field_validator("metrics", "dimensions")
    @classmethod
    def _no_duplicates(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError(f"duplicate names in {values}")
        return values
