"""The two cadences (DESIGN.md §5): daily re-pull of 7 days, weekly reconcile of 28 days."""

from __future__ import annotations

from datetime import date
from typing import Literal

from omw.sources.windows import Window, trailing_window

SyncMode = Literal["daily", "weekly"]

CADENCES: dict[SyncMode, int] = {"daily": 7, "weekly": 28}
"""Days each cadence re-fetches, ending yesterday. Meta restates for up to 28 days."""


def cadence_window(mode: SyncMode, today: date) -> Window:
    return trailing_window(today, CADENCES[mode])
