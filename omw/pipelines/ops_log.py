"""Writing run events to ``ops`` with the pipeline's own warehouse credentials."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import clickhouse_connect
import dlt
from clickhouse_connect.driver.client import Client

PACING_EVENTS = "ops.pacing_events"
PACING_EVENT_COLUMNS = (
    "recorded_at",
    "source",
    "account_id",
    "mode",
    "event",
    "reason",
    "utilisation",
    "sleep_seconds",
    "detail",
)


@dataclass(frozen=True)
class PacingEvent:
    source: str
    account_id: str
    mode: str
    event: str
    reason: str = ""
    utilisation: float = 0.0
    sleep_seconds: float = 0.0
    detail: str = ""
    recorded_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC).replace(tzinfo=None))

    def row(self) -> list[Any]:
        return [getattr(self, name) for name in PACING_EVENT_COLUMNS]


@contextmanager
def warehouse_client(pipeline: dlt.Pipeline) -> Iterator[Client]:
    """A clickhouse-connect client with the pipeline destination's credentials (the `pipeline` user)."""
    credentials: Any = pipeline.destination_client().config.credentials
    if credentials is None:
        raise RuntimeError("the pipeline destination has no credentials configured")
    client = clickhouse_connect.get_client(
        host=credentials.host,
        port=credentials.http_port,
        username=credentials.username,
        password=credentials.password,
        secure=bool(credentials.secure),
    )
    try:
        yield client
    finally:
        client.close()


def record_pacing_events(client: Client, events: Sequence[PacingEvent]) -> int:
    if not events:
        return 0
    client.insert(PACING_EVENTS, [event.row() for event in events], column_names=list(PACING_EVENT_COLUMNS))
    return len(events)
