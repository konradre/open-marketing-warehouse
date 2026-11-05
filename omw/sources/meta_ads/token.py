"""System-user token expiry (DESIGN.md §5): record it, fail loudly rather than go silently stale."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

WARN_DAYS = 7


@dataclass(frozen=True)
class TokenStatus:
    expires_at: date | None
    days_left: int | None
    expired: bool
    expiring: bool

    @property
    def event(self) -> str | None:
        if self.expired:
            return "token_expired"
        if self.expiring:
            return "token_expiring"
        return None

    @property
    def detail(self) -> str:
        if self.expires_at is None:
            return "token expiry not configured (sources.meta_ads.token_expires_at)"
        return f"token expires {self.expires_at.isoformat()} ({self.days_left} days)"


def check_token(expires_at: date | str | None, today: date, warn_days: int = WARN_DAYS) -> TokenStatus:
    """``expired`` when the date has passed, ``expiring`` within ``warn_days``; unknown expiry is neither."""
    if expires_at is None or expires_at == "":
        return TokenStatus(None, None, False, False)
    if isinstance(expires_at, str):
        expires_at = date.fromisoformat(expires_at)
    days_left = (expires_at - today).days
    return TokenStatus(expires_at, days_left, days_left < 0, days_left <= warn_days and days_left >= 0)


class TokenExpiredError(RuntimeError):
    def __init__(self, status: TokenStatus) -> None:
        super().__init__(
            f"the Meta system-user token expired on {status.expires_at}: replace it before syncing "
            "(sources.meta_ads.access_token / token_expires_at)"
        )
        self.status = status
