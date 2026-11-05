"""The pacer: sleep in proportion above ``slow_at``, stop cleanly at ``stop_at`` or on a rate-limit error.

Install it on the ``requests`` session the Meta SDK uses (``MetaPacer.install``). It wraps ``session.send``:
every response is read for usage headers; above ``slow_at`` the next request is delayed in proportion to
the excess; at ``stop_at``, when Meta says access is blocked, or when the body carries a rate-limit error
code (4 and its relatives), it raises ``PacerStop`` and keeps raising it for every later request, so the
run ends instead of hammering the limit. The source catches ``PacerStop`` at resource level and ends the
run with the complete days it has; the pipeline writes the reason to ``ops``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import requests

from omw.pacing.headers import Usage, read_usage

log = logging.getLogger(__name__)

RATE_LIMIT_ERROR_CODES: frozenset[int] = frozenset({4, 17, 32, 613, *range(80000, 80015)})
"""Graph API error codes that mean "you hit a limit": 4 app-level, 17 user-level, 32 page-level, 613 custom
rate limit, 80000-80014 the per-use-case ads limits (80000 and 80004 are the ad-account insights ones).
A stop, never a retry."""


class StopReason(StrEnum):
    UTILISATION = "utilisation"
    RATE_LIMITED = "rate_limited"
    ACCESS_BLOCKED = "access_blocked"


class PacerStop(RuntimeError):  # noqa: N818 - a control signal ("stop the run"), not an error
    def __init__(self, reason: StopReason, utilisation: float, detail: str) -> None:
        super().__init__(f"meta pacing stop ({reason}): {detail}")
        self.reason = reason
        self.utilisation = utilisation
        self.detail = detail


@dataclass(frozen=True)
class PacerConfig:
    slow_at: float = 75.0
    """Utilisation percentage above which requests are delayed."""
    stop_at: float = 95.0
    """Utilisation percentage at which the run ends. "Near 100%" in DESIGN.md §5."""
    max_sleep_seconds: float = 300.0
    """Delay applied just below ``stop_at``; delays scale linearly from 0 at ``slow_at``."""

    def __post_init__(self) -> None:
        if not 0 <= self.slow_at < self.stop_at <= 100:
            raise ValueError(f"need 0 <= slow_at < stop_at <= 100, got {self.slow_at}, {self.stop_at}")
        if self.max_sleep_seconds < 0:
            raise ValueError("max_sleep_seconds must not be negative")


@dataclass(frozen=True)
class Decision:
    utilisation: float
    source: str
    sleep_seconds: float = 0.0
    stop: StopReason | None = None
    detail: str = ""
    tier: str | None = None


@dataclass
class PacerSummary:
    requests: int = 0
    peak_utilisation: float = 0.0
    total_sleep_seconds: float = 0.0
    sleeps: int = 0
    stop: StopReason | None = None
    stop_detail: str = ""
    tier: str | None = None
    decisions: list[Decision] = field(default_factory=list)


def rate_limit_error(status_code: int, body: str) -> tuple[int, str] | None:
    """``(code, message)`` when an error body carries a rate-limit code, else None. Only error statuses
    are parsed; a successful page is never decoded here."""
    if status_code < 400 or not body:
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    if isinstance(code, int) and code in RATE_LIMIT_ERROR_CODES:
        return code, str(error.get("message", ""))
    return None


class MetaPacer:
    def __init__(
        self,
        config: PacerConfig | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or PacerConfig()
        self._sleep = sleep
        self.summary = PacerSummary()
        self.stopped: PacerStop | None = None

    # ---- decisions ------------------------------------------------------------------------------------

    def sleep_for(self, utilisation: float) -> float:
        """Seconds to wait at ``utilisation``: 0 up to ``slow_at``, then linear to ``max_sleep_seconds``."""
        c = self.config
        if utilisation <= c.slow_at:
            return 0.0
        fraction = (utilisation - c.slow_at) / (c.stop_at - c.slow_at)
        return round(min(1.0, fraction) * c.max_sleep_seconds, 3)

    def decide(self, usage: Usage) -> Decision:
        pct = usage.pct
        if usage.regain_access_minutes > 0:
            detail = f"Meta blocks this use case for about {usage.regain_access_minutes} min ({usage.source})"
            return Decision(pct, usage.source, 0.0, StopReason.ACCESS_BLOCKED, detail, usage.tier)
        if pct >= self.config.stop_at:
            detail = f"utilisation {pct:.1f}% >= stop_at {self.config.stop_at:.0f}% ({usage.source})"
            return Decision(pct, usage.source, 0.0, StopReason.UTILISATION, detail, usage.tier)
        return Decision(pct, usage.source, self.sleep_for(pct), None, "", usage.tier)

    def observe(self, headers: Mapping[str, str]) -> Decision:
        return self.decide(read_usage(headers))

    def check(self, status_code: int, headers: Mapping[str, str], body: str = "") -> Decision:
        """The decision for one response: a rate-limit error wins over the headers."""
        limited = rate_limit_error(status_code, body)
        if limited:
            code, message = limited
            usage = read_usage(headers)
            detail = f"Graph API error {code}: {message}"
            return Decision(usage.pct, usage.source, 0.0, StopReason.RATE_LIMITED, detail, usage.tier)
        return self.observe(headers)

    # ---- effects --------------------------------------------------------------------------------------

    def apply(self, decision: Decision) -> None:
        """Record the decision, then sleep or raise ``PacerStop``."""
        s = self.summary
        s.requests += 1
        s.peak_utilisation = max(s.peak_utilisation, decision.utilisation)
        s.decisions.append(decision)
        if decision.tier:
            s.tier = decision.tier
        if decision.stop is not None:
            s.stop = decision.stop
            s.stop_detail = decision.detail
            self.stopped = PacerStop(decision.stop, decision.utilisation, decision.detail)
            log.warning("meta pacer stopping the run: %s", decision.detail)
            raise self.stopped
        if decision.sleep_seconds > 0:
            s.sleeps += 1
            s.total_sleep_seconds += decision.sleep_seconds
            log.info(
                "meta pacer: utilisation %.1f%% (%s), sleeping %.1fs",
                decision.utilisation,
                decision.source,
                decision.sleep_seconds,
            )
            self._sleep(decision.sleep_seconds)

    def install(self, session: requests.Session) -> requests.Session:
        """Wrap ``session.send`` so every response is paced and a stopped pacer fails fast."""
        original_send = session.send
        pacer = self

        def paced_send(request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
            if pacer.stopped is not None:
                raise pacer.stopped
            response = original_send(request, **kwargs)
            body = response.text if response.status_code >= 400 else ""
            pacer.apply(pacer.check(response.status_code, response.headers, body))
            return response

        session.send = paced_send  # type: ignore[method-assign]
        return session
