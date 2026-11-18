"""``compare``: which window is the one before, and how the answer states it."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from omw.metrics import Catalog, MetricRequest, Mood, TimeRange, compile_request
from omw.metrics.request import Comparison
from omw.metrics.runner import answer_request
from omw.metrics.server import query_metrics_payload
from tests.unit.metrics.conftest import TODAY


def _range(start: str, end: str) -> TimeRange:
    return TimeRange(start=date.fromisoformat(start), end=date.fromisoformat(end))


@pytest.mark.parametrize(
    ("compare", "current", "previous"),
    [
        # A calendar month against the whole calendar month before, whatever the two lengths.
        ("month", ("2026-09-01", "2026-09-30"), ("2026-08-01", "2026-08-31")),
        ("month", ("2026-03-01", "2026-03-31"), ("2026-02-01", "2026-02-28")),
        ("month", ("2026-02-01", "2026-02-28"), ("2026-01-01", "2026-01-31")),
        ("month", ("2026-01-01", "2026-01-31"), ("2025-12-01", "2025-12-31")),
        ("month", ("2024-03-01", "2024-03-31"), ("2024-02-01", "2024-02-29")),
        # Whole months, several of them: a quarter against the quarter shifted one month.
        ("month", ("2026-07-01", "2026-09-30"), ("2026-06-01", "2026-08-31")),
        # Month to date against the same days of the month before.
        ("month", ("2026-10-01", "2026-10-05"), ("2026-09-01", "2026-09-05")),
        # A rolling window keeps its length where the calendar allows: no snapping to the month end.
        ("month", ("2026-01-30", "2026-02-28"), ("2025-12-30", "2026-01-28")),
        # A day the month before does not have is held at that month's last day.
        ("month", ("2026-03-31", "2026-03-31"), ("2026-02-28", "2026-02-28")),
        ("month", ("2026-03-29", "2026-03-30"), ("2026-02-28", "2026-02-28")),
        # A year: the same calendar period, a leap day included or held.
        ("year", ("2026-09-01", "2026-09-30"), ("2025-09-01", "2025-09-30")),
        ("year", ("2025-02-01", "2025-02-28"), ("2024-02-01", "2024-02-29")),
        ("year", ("2024-02-01", "2024-02-29"), ("2023-02-01", "2023-02-28")),
        ("year", ("2024-02-29", "2024-02-29"), ("2023-02-28", "2023-02-28")),
        ("year", ("2026-01-01", "2026-12-31"), ("2025-01-01", "2025-12-31")),
        ("year", ("2026-10-01", "2026-10-03"), ("2025-10-01", "2025-10-03")),
        # A week: the same weekdays seven days earlier.
        ("week", ("2026-09-21", "2026-09-27"), ("2026-09-14", "2026-09-20")),
        ("week", ("2026-10-01", "2026-10-03"), ("2026-09-24", "2026-09-26")),
        # The same number of days immediately before.
        ("previous_period", ("2026-09-01", "2026-09-30"), ("2026-08-02", "2026-08-31")),
    ],
)
def test_the_window_before(compare: str, current: tuple[str, str], previous: tuple[str, str]) -> None:
    assert _range(*current).shifted(compare) == _range(*previous)  # type: ignore[arg-type]


def test_a_week_comparison_keeps_the_weekdays() -> None:
    current = _range("2026-09-21", "2026-09-27")  # Monday to Sunday
    previous = current.shifted("week")
    assert (current.start.weekday(), previous.start.weekday()) == (0, 0)
    assert previous.end.weekday() == 6 and previous.days == 7 and current.overlaps(previous) == 0


def test_september_against_august_in_sql_and_in_the_answer(catalog: Catalog) -> None:
    request = MetricRequest(
        metrics=["meta_spend"],
        filters={"currency": "USD"},
        time_range=_range("2026-09-01", "2026-09-30"),
        compare="month",
    )
    plan = compile_request(catalog, request, date(2026, 12, 1))
    assert "if(date BETWEEN '2026-09-01' AND '2026-09-30', 'current', 'previous') AS period" in plan.sql
    assert "(date BETWEEN '2026-08-01' AND '2026-08-31')" in plan.sql  # all 31 days of August
    assert plan.comparison == Comparison.model_validate(
        {
            "compare": "month",
            "current": {"start": "2026-09-01", "end": "2026-09-30", "days": 30},
            "previous": {"start": "2026-08-01", "end": "2026-08-31", "days": 31},
        }
    )
    assert plan.caveats == [
        "meta_ads.campaigns: Meta restates conversions and spend for up to 28 days; recent days change."
    ]


def _execute(sql: str) -> tuple[list[str], list[list[Any]]]:
    if "ops.check_verdicts" in sql:
        return ["source", "check", "report", "status", "detail"], []
    return ["period", "sessions"], [["current", 10], ["previous", 8]]


def test_the_answer_states_the_compared_window_as_a_structured_field(catalog: Catalog) -> None:
    payload = query_metrics_payload(
        catalog, _execute, ["sessions"], "last_month", compare="month", today=date(2026, 10, 10)
    )
    assert payload["mood"] == "serve" and payload["rows"] == [["current", 10], ["previous", 8]]
    assert payload["comparison"] == {
        "compare": "month",
        "current": {"start": "2026-09-01", "end": "2026-09-30", "days": 30},
        "previous": {"start": "2026-08-01", "end": "2026-08-31", "days": 31},
        "overlap_days": 0,
    }
    plain = query_metrics_payload(catalog, _execute, ["sessions"], "last_month", today=date(2026, 10, 10))
    assert plain["comparison"] is None


def test_windows_that_overlap_count_the_shared_days_in_both_periods(catalog: Catalog) -> None:
    """``week`` on thirty days: 23 days are in both windows. Each period must still hold all of its days."""
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["sessions"], time_range=_range("2026-06-01", "2026-06-30"), compare="week"),
        _execute,
        TODAY,
    )
    assert answer.comparison is not None and answer.comparison.overlap_days == 23
    assert answer.comparison.previous.start == date(2026, 5, 25) and answer.comparison.previous.days == 30
    assert (
        "arrayJoin(arrayConcat(if(date BETWEEN '2026-06-01' AND '2026-06-30', ['current'], []), "
        "if(date BETWEEN '2026-05-25' AND '2026-06-23', ['previous'], []))) AS period"
    ) in answer.sql
    assert "'current', 'previous') AS period" not in answer.sql  # that form drops shared days from previous
    assert answer.mood is Mood.DISCLOSE
    assert answer.caveats == [
        "the compared windows share 23 days (2026-06-01 to 2026-06-23); those days count in both periods"
    ]


def test_a_shorter_month_before_is_disclosed(catalog: Catalog) -> None:
    plan = compile_request(
        catalog,
        MetricRequest(metrics=["sessions"], time_range=_range("2026-03-29", "2026-03-31"), compare="month"),
        TODAY,
    )
    assert plan.comparison is not None and plan.comparison.previous.days == 1
    assert plan.caveats == [
        "the previous window (2026-02-28 to 2026-02-28) has 1 day against 3 days: "
        "the earlier month is shorter"
    ]
    whole = compile_request(
        catalog,
        MetricRequest(metrics=["sessions"], time_range=_range("2026-03-01", "2026-03-31"), compare="month"),
        TODAY,
    )
    assert whole.caveats == []  # 31 days against February's 28 is what a calendar month means
