from __future__ import annotations

from datetime import date

import pytest

from omw.sources.search_console.windows import Window, backfill_chunks, daily_window, retention_start


def test_daily_window_is_ten_days_ending_yesterday() -> None:
    window = daily_window(date(2026, 11, 4))
    assert window == Window(date(2026, 10, 25), date(2026, 11, 3))
    assert window.days == 10


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 4), date(2025, 7, 4)),
        (date(2026, 3, 31), date(2024, 11, 30)),  # day clamped to the shorter month
        (date(2026, 1, 15), date(2024, 9, 15)),
    ],
)
def test_retention_start_is_sixteen_calendar_months_back(today: date, expected: date) -> None:
    assert retention_start(today) == expected


def test_backfill_chunks_are_oldest_first_90_days_and_stop_before_the_daily_window() -> None:
    today = date(2026, 11, 4)
    chunks = backfill_chunks(today, completed_through=None)
    assert chunks[0].start == retention_start(today)
    assert all(chunk.days == 90 for chunk in chunks[:-1])
    assert chunks[-1].end == daily_window(today).start.replace(day=24)  # 2026-10-24
    assert chunks == sorted(chunks)
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert (later.start - earlier.end).days == 1


def test_backfill_resumes_after_the_completed_chunk() -> None:
    today = date(2026, 11, 4)
    full = backfill_chunks(today, completed_through=None)
    resumed = backfill_chunks(today, completed_through=full[1].end)
    assert resumed == full[2:]


def test_backfill_is_empty_when_done() -> None:
    today = date(2026, 11, 4)
    assert backfill_chunks(today, completed_through=daily_window(today).start) == []


def test_window_rejects_reversed_dates() -> None:
    with pytest.raises(ValueError):
        Window(date(2026, 1, 2), date(2026, 1, 1))
