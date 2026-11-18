"""When a job runs: a time of day in UTC, every day or on named weekdays.

    "03:15"            every day at 03:15 UTC
    "sun 03:15"        Sundays
    "mon,thu 03:15"    Mondays and Thursdays
    "off"              never: the job is disabled

A schedule has *slots*, the instants it fires. The scheduler runs a job when its latest slot has not been
attempted yet, so a host that was down at 03:15 catches up once when it comes back and never runs a
missed slot twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
"""In ``datetime.weekday()`` order."""
OFF = "off"
_SPEC = re.compile(r"^(?:(?P<days>[a-z]{3}(?:,[a-z]{3})*)\s+)?(?P<hour>\d{1,2}):(?P<minute>\d{2})$")


class ScheduleError(ValueError):
    """A schedule line that cannot be read."""


@dataclass(frozen=True)
class Schedule:
    hour: int
    minute: int
    weekdays: frozenset[int] = frozenset(range(7))
    """Days it fires on, Monday = 0."""

    @property
    def daily(self) -> bool:
        return len(self.weekdays) == 7

    def _at(self, day: datetime) -> datetime:
        return day.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)

    def latest(self, now: datetime) -> datetime:
        """The most recent slot at or before ``now``."""
        now = now.astimezone(UTC)
        for back in range(8):
            slot = self._at(now - timedelta(days=back))
            if slot <= now and slot.weekday() in self.weekdays:
                return slot
        raise AssertionError("a schedule fires at least once a week")  # pragma: no cover

    def next(self, now: datetime) -> datetime:
        """The first slot after ``now``."""
        now = now.astimezone(UTC)
        for ahead in range(8):
            slot = self._at(now + timedelta(days=ahead))
            if slot > now and slot.weekday() in self.weekdays:
                return slot
        raise AssertionError("a schedule fires at least once a week")  # pragma: no cover

    def without(self, weekdays: frozenset[int]) -> Schedule | None:
        """The same time on the remaining days, or ``None`` when no day is left."""
        left = self.weekdays - weekdays
        return Schedule(self.hour, self.minute, left) if left else None

    def __str__(self) -> str:
        time = f"{self.hour:02d}:{self.minute:02d}"
        if self.daily:
            return time
        return f"{','.join(WEEKDAYS[day] for day in sorted(self.weekdays))} {time}"

    def on_calendar(self) -> str:
        """The schedule as a systemd ``OnCalendar=`` expression, in UTC."""
        days = (
            "" if self.daily else ",".join(WEEKDAYS[day].capitalize() for day in sorted(self.weekdays)) + " "
        )
        return f"{days}*-*-* {self.hour:02d}:{self.minute:02d}:00 UTC"


def parse(spec: str) -> Schedule | None:
    """A schedule line as a ``Schedule``; ``None`` for ``off``."""
    text = str(spec).strip().lower()
    if text in (OFF, ""):
        return None
    match = _SPEC.match(text)
    if not match:
        raise ScheduleError(
            f'cannot read the schedule {spec!r}: write "HH:MM" (UTC, every day), "sun HH:MM", '
            f'"mon,thu HH:MM" or "{OFF}"'
        )
    hour, minute = int(match["hour"]), int(match["minute"])
    if hour > 23 or minute > 59:
        raise ScheduleError(
            f"cannot read the schedule {spec!r}: {hour:02d}:{minute:02d} is not a time of day"
        )
    weekdays = frozenset(range(7))
    if match["days"]:
        names = match["days"].split(",")
        unknown = [name for name in names if name not in WEEKDAYS]
        if unknown:
            raise ScheduleError(
                f"cannot read the schedule {spec!r}: {', '.join(unknown)} is not one of {', '.join(WEEKDAYS)}"
            )
        weekdays = frozenset(WEEKDAYS.index(name) for name in names)
    return Schedule(hour, minute, weekdays)
