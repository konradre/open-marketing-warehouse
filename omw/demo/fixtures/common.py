"""Helpers the fixture builders share: exact splits, synthetic ids and the time formats the APIs use."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from omw.demo.world import Day
from omw.eval.generate.rng import Rng, split

Payloads = dict[str, Any]
"""Fixture file name (without ``.json``) -> the payload an API answers with."""


def parts(rng: Rng, total: Day, weights: Sequence[float], *keys: object) -> list[Day]:
    """A campaign day shared out over the members of a breakdown, as whole numbers that add up to it.

    The shares depend on the keys only, never on when the snapshot is taken, so a restated day keeps the
    same mix."""
    impressions = split(total.impressions, rng.jitter(weights, 0.15, *keys, "impressions"))
    clicks = split(total.clicks, rng.jitter(weights, 0.25, *keys, "clicks"))
    cents = split(total.cents, rng.jitter(weights, 0.25, *keys, "cost"))
    conversions = split(total.conversions, rng.jitter(weights, 0.3, *keys, "conversions"))
    return [Day(*values) for values in zip(impressions, clicks, cents, conversions, strict=True)]


def share(total: int, weights: Sequence[float]) -> list[int]:
    return split(total, list(weights))


def uuid(*keys: object) -> str:
    """A stable UUID-shaped id for these keys."""
    h = hashlib.sha256("|".join(str(key) for key in keys).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}"


def iso_z(moment: datetime | None) -> str | None:
    """``2026-10-01T10:00:00.000Z``, as Twenty writes timestamps."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z") if moment else None


def iso_offset(moment: datetime) -> str:
    """``2026-10-01T10:00:00+0000``, as the Graph API writes them."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")


def epoch(moment: datetime | None) -> int | None:
    return int(moment.timestamp()) if moment else None


def epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def money(cents: int) -> str:
    return f"{cents / 100:.2f}"
