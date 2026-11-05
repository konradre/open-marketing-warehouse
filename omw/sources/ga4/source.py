"""The dlt source: one merge resource per grain replacing its ``(property_id, date)`` slice, and a log."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import Any

import dlt
from dlt.common.schema.typing import TMergeDispositionDict
from dlt.destinations.adapters import clickhouse_adapter
from dlt.extract import DltResource

from omw.sources.columns import column
from omw.sources.ga4.client import GA4Client, service_account_token_provider
from omw.sources.ga4.grains import GRAINS, MERGE_KEY, METRICS, Grain, column_hints, shape_row
from omw.sources.windows import Window, trailing_window

SOURCE_NAME = "ga4"
RE_PULL_DAYS = 7
"""GA4 revises the last 2-3 days (processing lag, modelled data); a week covers it with margin."""

MERGE_DISPOSITION: TMergeDispositionDict = {"disposition": "merge", "strategy": "delete-insert"}


def _grain_resource(
    grain: Grain, client: GA4Client, property_ids: Sequence[str], window: Window
) -> DltResource:
    def rows() -> Iterator[dict[str, Any]]:
        for property_id in property_ids:
            for api_row in client.run_report(
                property_id, window.start, window.end, grain.api_dimensions, METRICS
            ):
                yield shape_row(grain, property_id, api_row)

    resource = dlt.resource(
        rows,
        name=grain.name,
        write_disposition=MERGE_DISPOSITION,
        merge_key=list(MERGE_KEY),
        columns=column_hints(grain),
    )
    return clickhouse_adapter(resource, sort=list(grain.sort_key))


def _sync_windows_resource(property_ids: Sequence[str], window: Window) -> DltResource:
    def rows() -> Iterator[dict[str, Any]]:
        loaded_at = datetime.now(tz=UTC)
        for property_id in property_ids:
            yield {
                "property_id": property_id,
                "mode": "daily",
                "window_start": window.start,
                "window_end": window.end,
                "loaded_at": loaded_at,
            }

    resource = dlt.resource(
        rows,
        name="sync_windows",
        write_disposition="append",
        columns={
            "property_id": column("text"),
            "mode": column("text"),
            "window_start": column("date"),
            "window_end": column("date"),
            "loaded_at": column("timestamp"),
        },
    )
    return clickhouse_adapter(resource, sort=["property_id", "window_start"])


@dlt.source(name=SOURCE_NAME, max_table_nesting=0)
def ga4(
    property_ids: Sequence[str] = dlt.config.value,
    credentials_path: str | None = dlt.secrets.value,
    window: Window | None = None,
    days: int = RE_PULL_DAYS,
    grains: Sequence[str] = tuple(GRAINS),
    client: GA4Client | None = None,
    today: date | None = None,
) -> Iterator[DltResource]:
    """GA4 reports for ``window`` (default: the last ``days`` complete days, ending yesterday).

    ``property_ids`` are numeric GA4 property ids. ``client`` is injectable for tests; otherwise a
    service-account client from ``credentials_path``.
    """
    if not property_ids:
        raise ValueError("ga4 needs at least one property id (sources.ga4.property_ids)")
    unknown = set(grains) - set(GRAINS)
    if unknown:
        raise ValueError(f"unknown grains {sorted(unknown)}; choose from {sorted(GRAINS)}")
    if client is None:
        if not credentials_path:
            raise ValueError("ga4 needs credentials_path (sources.ga4.credentials_path)")
        client = GA4Client(service_account_token_provider(credentials_path))
    properties = [str(p).removeprefix("properties/") for p in property_ids]
    window = window or trailing_window(today or date.today(), days)

    for name in grains:
        yield _grain_resource(GRAINS[name], client, properties, window)
    yield _sync_windows_resource(properties, window)
