"""Google Search Console source (docs/THREADS.md T2).

One dlt resource per report grain over ``searchanalytics.query``: ``core`` (by date), ``queries``, ``pages``,
``query_page``, ``countries`` and ``devices``. Every row carries ``source``, ``report``, ``site_url``,
``search_type`` and ``date``. Each run fetches one window and replaces that ``(site_url, search_type, date)``
slice in the warehouse; ``windows`` computes the rolling daily window and the 90-day backfill chunks.
"""

from omw.sources.search_console.client import (
    SearchConsoleAccessError,
    SearchConsoleClient,
    service_account_token_provider,
)
from omw.sources.search_console.grains import GRAINS, Grain, shape_row
from omw.sources.search_console.source import SOURCE_NAME, backfill_progress, search_console
from omw.sources.search_console.windows import Window, backfill_chunks, daily_window, retention_start

__all__ = [
    "GRAINS",
    "SOURCE_NAME",
    "Grain",
    "SearchConsoleAccessError",
    "SearchConsoleClient",
    "Window",
    "backfill_chunks",
    "backfill_progress",
    "daily_window",
    "retention_start",
    "search_console",
    "service_account_token_provider",
    "shape_row",
]
