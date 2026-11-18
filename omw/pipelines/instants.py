"""Timestamps the pipeline side writes to the warehouse, as instants.

clickhouse-connect reads a naive ``datetime`` in the host's time zone. A UTC wall-clock time with the zone
stripped is therefore stored shifted by the host's offset whenever the host is not on UTC, while rows
written in SQL carry the server's own ``now()``. Every writer passes its timestamp through ``instant`` so
the stored time is the same on any host.
"""

from __future__ import annotations

from datetime import UTC, datetime


def instant(now: datetime | None = None) -> datetime:
    """``now`` as an instant in UTC: the current time when unset, a naive value taken as UTC wall-clock."""
    now = now or datetime.now(tz=UTC)
    return now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)


def utc_wall_clock(now: datetime | None = None) -> datetime:
    """``instant(now)`` with the zone dropped, for arithmetic against the naive UTC times the driver returns
    and for SQL literals compared with ``DateTime`` columns on a UTC server."""
    return instant(now).replace(tzinfo=None)
