"""An inclusive date window, shared by the sources that re-pull rolling ranges."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, order=True)
class Window:
    start: date
    end: date
    """Inclusive on both ends."""

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"window ends before it starts: {self.start} > {self.end}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def __iter__(self):  # type: ignore[no-untyped-def]
        """Every day of the window, oldest first."""
        for offset in range(self.days):
            yield self.start + timedelta(days=offset)

    def __str__(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


def trailing_window(today: date, days: int) -> Window:
    """The last ``days`` complete days, ending yesterday."""
    end = today - timedelta(days=1)
    return Window(end - timedelta(days=days - 1), end)
