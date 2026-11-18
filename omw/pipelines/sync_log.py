"""The sync log, written last and only by a run that synced.

Every pipeline appends one row per run to ``raw.<source>___sync_windows``; the freshness check reads its
latest ``loaded_at`` as the last successful sync (DESIGN.md §7). As one resource among the others the row
is extracted first, before any data is fetched (dlt takes one item from every resource in turn), so a run
that then pulled nothing, or that the Meta pacer ended early, still logged a sync and the source looked
fresh while its data aged.

``run_then_log`` runs the same source in two parts inside one ``pipeline.run``: every data resource, then
the log. dlt extracts the parts in order into one load package, so the log row is decided when the data is
in, and loads with it or not at all.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import dlt
from dlt.extract import DltSource

SYNC_LOG = "sync_windows"


def run_then_log(
    pipeline: dlt.Pipeline,
    source: DltSource,
    require_rows: str | None = None,
    synced: Callable[[], bool] | None = None,
) -> None:
    """Load ``source``; write its sync log row only when the run was a sync.

    ``require_rows`` names a resource that must yield at least one row (the ``core`` grain of a report
    source). ``synced`` is asked once the data is extracted (the Meta pacer: did it stop). Without either
    the row is always written, after the data.
    """
    names = [name for name in source.resources.selected if name != SYNC_LOG]
    data = source.with_resources(*names)
    log = source.with_resources(SYNC_LOG)
    seen = 0

    def count(item: Any) -> Any:
        nonlocal seen
        seen += 1
        return item

    def is_sync(_row: Any) -> bool:
        return (require_rows is None or seen > 0) and (synced is None or synced())

    if require_rows is not None:
        data.resources[require_rows].add_map(count)
    log.resources[SYNC_LOG].add_filter(is_sync)
    pipeline.run([data, log])
