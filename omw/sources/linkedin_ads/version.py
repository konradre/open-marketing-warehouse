"""The pinned ``LinkedIn-Version`` and its support window.

LinkedIn releases a Marketing API version each month (``YYYYMM``) and supports it for 12 months. Pin one
version here; ``check_version`` warns three months before sunset and fails after it, so a run never
silently calls a version LinkedIn has retired.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

LINKEDIN_VERSION = "202610"
"""The one place the version lives. Bump it after reading LinkedIn's migration notes for the new month."""

SUPPORT_MONTHS = 12
WARN_MONTHS = 3
_VERSION = re.compile(r"^(\d{4})(0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class VersionStatus:
    version: str
    released: date
    sunset: date
    months_left: int
    expired: bool
    expiring: bool

    @property
    def message(self) -> str:
        if self.expired:
            return (
                f"LinkedIn-Version {self.version} left support on {self.sunset}: bump LINKEDIN_VERSION in "
                "omw/sources/linkedin_ads/version.py after reading the migration notes"
            )
        if self.expiring:
            return (
                f"LinkedIn-Version {self.version} leaves support on {self.sunset} "
                f"({self.months_left} months): plan the bump"
            )
        return f"LinkedIn-Version {self.version} is supported until {self.sunset}"


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def check_version(version: str = LINKEDIN_VERSION, today: date | None = None) -> VersionStatus:
    match = _VERSION.match(version)
    if not match:
        raise ValueError(f"LinkedIn-Version must be YYYYMM, got {version!r}")
    today = today or date.today()
    released = date(int(match.group(1)), int(match.group(2)), 1)
    sunset = _add_months(released, SUPPORT_MONTHS)
    months_left = (sunset.year - today.year) * 12 + (sunset.month - today.month) - (today.day > 1)
    return VersionStatus(
        version=version,
        released=released,
        sunset=sunset,
        months_left=max(months_left, 0),
        expired=today >= sunset,
        expiring=today < sunset and months_left <= WARN_MONTHS,
    )


class VersionExpiredError(RuntimeError):
    def __init__(self, status: VersionStatus) -> None:
        super().__init__(status.message)
        self.status = status
