"""The dlt source: two performance resources replacing their slice, campaign entities merged on id, a log."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import Any

import dlt
from dlt.common.schema.typing import TMergeDispositionDict
from dlt.destinations.adapters import clickhouse_adapter
from dlt.extract import DltResource

from omw.sources.google_ads.client import API_VERSION, GoogleAdsClient, oauth_token_provider
from omw.sources.google_ads.queries import REPORTS, Report, entities_query, performance_query
from omw.sources.google_ads.rows import (
    ENTITY_COLUMNS,
    MERGE_KEY,
    performance_columns,
    shape_entity,
    shape_performance,
)
from omw.sources.search_console.grains import column
from omw.sources.windows import Window, trailing_window

SOURCE_NAME = "google_ads"
RE_PULL_DAYS = 30
"""Google attributes conversions for up to 30 days after the click; the daily run re-pulls that much."""

MERGE_DISPOSITION: TMergeDispositionDict = {"disposition": "merge", "strategy": "delete-insert"}


def _performance_resource(
    report: Report, client: GoogleAdsClient, customer_ids: Sequence[str], window: Window
) -> DltResource:
    def rows() -> Iterator[list[dict[str, Any]]]:
        query = performance_query(report, window)
        for customer_id in customer_ids:
            # One stream per customer and window; a failed stream raises before anything is yielded.
            yield [shape_performance(report, item) for item in client.search_stream(customer_id, query)]

    resource = dlt.resource(
        rows,
        name=f"performance_{report.name}",
        write_disposition=MERGE_DISPOSITION,
        merge_key=list(MERGE_KEY),
        columns=performance_columns(report),
    )
    return clickhouse_adapter(resource, sort=[*MERGE_KEY, *report.keys])


def _entities_resource(client: GoogleAdsClient, customer_ids: Sequence[str]) -> DltResource:
    def rows() -> Iterator[list[dict[str, Any]]]:
        for customer_id in customer_ids:
            yield [shape_entity(item) for item in client.search_stream(customer_id, entities_query())]

    resource = dlt.resource(
        rows, name="campaigns", write_disposition="merge", primary_key="campaign_id", columns=ENTITY_COLUMNS
    )
    return clickhouse_adapter(resource, sort=["campaign_id"])


def _sync_windows_resource(customer_ids: Sequence[str], window: Window) -> DltResource:
    def rows() -> Iterator[dict[str, Any]]:
        loaded_at = datetime.now(tz=UTC)
        for customer_id in customer_ids:
            yield {
                "customer_id": customer_id,
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
            "customer_id": column("text"),
            "mode": column("text"),
            "window_start": column("date"),
            "window_end": column("date"),
            "loaded_at": column("timestamp"),
        },
    )
    return clickhouse_adapter(resource, sort=["customer_id", "window_start"])


@dlt.source(name=SOURCE_NAME, max_table_nesting=0)
def google_ads(
    customer_ids: Sequence[str] = dlt.config.value,
    developer_token: str | None = dlt.secrets.value,
    client_id: str | None = dlt.secrets.value,
    client_secret: str | None = dlt.secrets.value,
    refresh_token: str | None = dlt.secrets.value,
    login_customer_id: str | None = dlt.config.value,
    api_version: str = API_VERSION,
    window: Window | None = None,
    reports: Sequence[str] = tuple(REPORTS),
    entities: bool = True,
    client: GoogleAdsClient | None = None,
    today: date | None = None,
) -> Iterator[DltResource]:
    """Google Ads performance and campaign entities for ``window`` (default: the last 30 complete days).

    ``customer_ids`` and ``login_customer_id`` (the manager account) without dashes. ``client`` is
    injectable for tests; otherwise an OAuth refresh-token client is built from the secrets.
    """
    if not customer_ids:
        raise ValueError("google_ads needs at least one customer id (sources.google_ads.customer_ids)")
    unknown = set(reports) - set(REPORTS)
    if unknown:
        raise ValueError(f"unknown reports {sorted(unknown)}; choose from {sorted(REPORTS)}")
    if client is None:
        if not (developer_token and client_id and client_secret and refresh_token):
            raise ValueError(
                "google_ads needs developer_token, client_id, client_secret and refresh_token "
                "(sources.google_ads.* in .dlt/secrets.toml)"
            )
        client = GoogleAdsClient(
            oauth_token_provider(client_id, client_secret, refresh_token),
            developer_token,
            login_customer_id,
            api_version=api_version,
        )
    customers = [c.replace("-", "") for c in customer_ids]
    window = window or trailing_window(today or date.today(), RE_PULL_DAYS)

    if entities:
        yield _entities_resource(client, customers)
    for name in reports:
        yield _performance_resource(REPORTS[name], client, customers, window)
    yield _sync_windows_resource(customers, window)
