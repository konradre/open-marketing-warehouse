"""The dlt source: one merge resource per grain plus a ``sync_windows`` log that carries backfill state."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal

import dlt
from dlt.common.schema.typing import TMergeDispositionDict
from dlt.destinations.adapters import clickhouse_adapter
from dlt.extract import DltResource

from omw.sources.search_console.client import SearchConsoleClient, service_account_token_provider
from omw.sources.search_console.grains import GRAINS, MERGE_KEY, Grain, column, column_hints, shape_row
from omw.sources.search_console.windows import Window, daily_window

SOURCE_NAME = "search_console"
SyncMode = Literal["daily", "backfill"]

MERGE_DISPOSITION: TMergeDispositionDict = {"disposition": "merge", "strategy": "delete-insert"}


def progress_key(site_url: str, search_type: str) -> str:
    return f"{site_url}|{search_type}"


def backfill_progress(state: dict[str, Any], site_url: str, search_type: str) -> date | None:
    """The last day a completed backfill chunk covered, from the source's dlt state."""
    done = state.get("backfill", {}).get(progress_key(site_url, search_type))
    return date.fromisoformat(done) if done else None


def _grain_resource(
    grain: Grain,
    client: SearchConsoleClient,
    site_urls: Sequence[str],
    search_types: Sequence[str],
    data_state: str,
    window: Window,
) -> DltResource:
    def rows() -> Iterator[dict[str, Any]]:
        for site_url in site_urls:
            for search_type in search_types:
                api_rows = client.query(
                    site_url, window.start, window.end, grain.api_dimensions, search_type, data_state
                )
                for api_row in api_rows:
                    yield shape_row(grain, site_url, search_type, api_row)

    resource = dlt.resource(
        rows,
        name=grain.name,
        write_disposition=MERGE_DISPOSITION,
        merge_key=list(MERGE_KEY),
        columns=column_hints(grain),
    )
    return clickhouse_adapter(resource, sort=list(grain.sort_key))


def _sync_windows_resource(
    site_urls: Sequence[str], search_types: Sequence[str], window: Window, mode: SyncMode
) -> DltResource:
    """One row per (property, search type) and run. In backfill mode it also records the chunk as
    completed in the source state, which only persists when the whole load succeeds."""

    def rows() -> Iterator[dict[str, Any]]:
        loaded_at = datetime.now(tz=UTC)
        for site_url in site_urls:
            for search_type in search_types:
                yield {
                    "site_url": site_url,
                    "search_type": search_type,
                    "mode": mode,
                    "window_start": window.start,
                    "window_end": window.end,
                    "loaded_at": loaded_at,
                }
                if mode == "backfill":
                    progress = dlt.current.source_state().setdefault("backfill", {})
                    progress[progress_key(site_url, search_type)] = window.end.isoformat()

    resource = dlt.resource(
        rows,
        name="sync_windows",
        write_disposition="append",
        columns={
            "site_url": column("text"),
            "search_type": column("text"),
            "mode": column("text"),
            "window_start": column("date"),
            "window_end": column("date"),
            "loaded_at": column("timestamp"),
        },
    )
    return clickhouse_adapter(resource, sort=["site_url", "search_type", "window_start"])


@dlt.source(name=SOURCE_NAME, max_table_nesting=0)
def search_console(
    site_urls: Sequence[str] = dlt.config.value,
    credentials_path: str | None = dlt.secrets.value,
    search_types: Sequence[str] = ("web",),
    data_state: str = "all",
    window: Window | None = None,
    mode: SyncMode = "daily",
    grains: Sequence[str] = tuple(GRAINS),
    client: SearchConsoleClient | None = None,
    today: date | None = None,
) -> Iterator[DltResource]:
    """Search Console reports for ``window`` (default: the rolling daily window ending yesterday).

    ``site_urls`` are properties as Search Console names them (``sc-domain:example.com`` or
    ``https://example.com/``). ``search_types``: web, image, video, news, discover, googleNews.
    ``data_state`` ``all`` includes the fresh, still-restated days; ``final`` waits for them to settle.
    ``client`` is injectable for tests; otherwise a service-account client from ``credentials_path``.
    """
    if client is None:
        if not credentials_path:
            raise ValueError(
                "search_console needs credentials_path (sources.search_console.credentials_path)"
            )
        client = SearchConsoleClient(service_account_token_provider(credentials_path))
    if not site_urls:
        raise ValueError("search_console needs at least one site_url")
    unknown = set(grains) - set(GRAINS)
    if unknown:
        raise ValueError(f"unknown grains {sorted(unknown)}; choose from {sorted(GRAINS)}")
    window = window or daily_window(today or date.today())

    for name in grains:
        yield _grain_resource(GRAINS[name], client, site_urls, search_types, data_state, window)
    yield _sync_windows_resource(site_urls, search_types, window, mode)
