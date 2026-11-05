"""Named time ranges resolve to explicit dates on a given today."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from omw.metrics import RELATIVE_RANGES, MetricRequest, TimeRange, relative_range

TODAY = date(2026, 3, 1)


@pytest.mark.parametrize(
    ("name", "start", "end"),
    [
        ("today", date(2026, 3, 1), date(2026, 3, 1)),
        ("yesterday", date(2026, 2, 28), date(2026, 2, 28)),
        ("last_7_days", date(2026, 2, 22), date(2026, 2, 28)),
        ("last_30_days", date(2026, 1, 30), date(2026, 2, 28)),
        ("last_90_days", date(2025, 12, 1), date(2026, 2, 28)),
        ("month_to_date", date(2026, 3, 1), date(2026, 3, 1)),
        ("last_month", date(2026, 2, 1), date(2026, 2, 28)),
        ("year_to_date", date(2026, 1, 1), date(2026, 3, 1)),
    ],
)
def test_every_named_range(name: str, start: date, end: date) -> None:
    assert name in RELATIVE_RANGES
    assert relative_range(name, TODAY) == TimeRange(start=start, end=end)  # type: ignore[arg-type]


def test_last_month_crosses_the_year() -> None:
    assert relative_range("last_month", date(2026, 1, 15)) == TimeRange(
        start=date(2025, 12, 1), end=date(2025, 12, 31)
    )


def test_request_window_is_explicit_or_named() -> None:
    explicit = MetricRequest.model_validate(
        {"metrics": ["m"], "time_range": {"start": "2026-01-01", "end": "2026-01-31"}}
    )
    assert explicit.window(TODAY) == TimeRange(start=date(2026, 1, 1), end=date(2026, 1, 31))
    named = MetricRequest(metrics=["m"], time_range="yesterday")
    assert named.window(TODAY) == TimeRange(start=date(2026, 2, 28), end=date(2026, 2, 28))
    with pytest.raises(ValidationError):
        MetricRequest.model_validate({"metrics": ["m"], "time_range": "last_fortnight"})
