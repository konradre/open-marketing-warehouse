"""Meta's usage headers, read into one utilisation figure.

Every Marketing API response may carry up to four JSON headers:

- ``x-fb-ads-insights-throttle``: ``app_id_util_pct``, ``acc_id_util_pct`` and the access tier.
- ``x-ad-account-usage``: ``acc_id_util_pct`` for the ad account, with ``reset_time_duration``.
- ``x-app-usage``: ``call_count``, ``total_cputime``, ``total_time``, each a percentage of the app's hourly
  budget.
- ``x-business-use-case-usage``: per business id, a list of ``{type, call_count, total_cputime, total_time,
  estimated_time_to_regain_access}`` entries; the last one is minutes until access returns when blocked.

The pacer acts on the highest percentage across all of them. Malformed headers are ignored, never fatal.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

INSIGHTS_THROTTLE = "x-fb-ads-insights-throttle"
AD_ACCOUNT_USAGE = "x-ad-account-usage"
APP_USAGE = "x-app-usage"
BUSINESS_USE_CASE_USAGE = "x-business-use-case-usage"
USAGE_HEADERS = (INSIGHTS_THROTTLE, AD_ACCOUNT_USAGE, APP_USAGE, BUSINESS_USE_CASE_USAGE)

_PCT_KEYS = ("app_id_util_pct", "acc_id_util_pct", "call_count", "total_cputime", "total_time")
_TIER_KEY = "ads_api_access_tier"
_REGAIN_KEY = "estimated_time_to_regain_access"


@dataclass(frozen=True)
class Reading:
    header: str
    metric: str
    pct: float


@dataclass(frozen=True)
class Usage:
    readings: tuple[Reading, ...]
    tier: str | None = None
    regain_access_minutes: int = 0
    """Minutes until access returns, when a business use case is blocked; 0 otherwise."""

    @property
    def peak(self) -> Reading | None:
        return max(self.readings, key=lambda r: r.pct) if self.readings else None

    @property
    def pct(self) -> float:
        peak = self.peak
        return peak.pct if peak else 0.0

    @property
    def source(self) -> str:
        peak = self.peak
        return f"{peak.header}:{peak.metric}" if peak else "none"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _parse(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _flat_readings(header: str, payload: Any, prefix: str = "") -> list[Reading]:
    if not isinstance(payload, dict):
        return []
    readings = []
    for key in _PCT_KEYS:
        pct = _number(payload.get(key))
        if pct is not None:
            readings.append(Reading(header, f"{prefix}{key}", pct))
    return readings


def read_usage(headers: Mapping[str, str]) -> Usage:
    """All percentage readings in ``headers`` (case-insensitive names), the tier and the regain time."""
    lowered = {str(name).lower(): value for name, value in headers.items()}
    readings: list[Reading] = []
    tier: str | None = None
    regain = 0

    for header in (INSIGHTS_THROTTLE, AD_ACCOUNT_USAGE, APP_USAGE):
        if header not in lowered:
            continue
        payload = _parse(lowered[header])
        readings.extend(_flat_readings(header, payload))
        if isinstance(payload, dict) and isinstance(payload.get(_TIER_KEY), str):
            tier = payload[_TIER_KEY]

    if BUSINESS_USE_CASE_USAGE in lowered:
        payload = _parse(lowered[BUSINESS_USE_CASE_USAGE])
        if isinstance(payload, dict):
            for business_id, entries in payload.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    use_case = str(entry.get("type", "unknown"))
                    readings.extend(
                        _flat_readings(BUSINESS_USE_CASE_USAGE, entry, f"{business_id}.{use_case}.")
                    )
                    minutes = _number(entry.get(_REGAIN_KEY))
                    if minutes is not None:
                        regain = max(regain, int(minutes))

    return Usage(tuple(readings), tier, regain)
