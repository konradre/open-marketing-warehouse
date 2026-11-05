"""Meta Ads sync entry points: ``daily`` (7-day re-pull) and ``weekly`` (28-day reconcile).

    uv run python -m omw.pipelines.meta_ads daily [--level campaign|ad ...]
    uv run python -m omw.pipelines.meta_ads weekly

The pacer wraps the SDK session (DESIGN.md §5). A pacer stop ends the run cleanly with the complete days it
has, exit code 0, and the reason in ``ops.pacing_events``; an expired token fails before any API call.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import dlt

from omw.pacing import MetaPacer, PacerStop
from omw.pipelines.marts import SQL_DIR, apply_marts
from omw.pipelines.ops_log import PacingEvent, record_pacing_events, warehouse_client
from omw.sources.meta_ads import SOURCE_NAME, InsightsLevel, SyncMode, check_token, meta_ads
from omw.sources.meta_ads.settings import LEVEL_REPORT
from omw.sources.meta_ads.token import TokenExpiredError

log = logging.getLogger(__name__)

PIPELINE_NAME = "meta_ads"
OPS_SQL = SQL_DIR / "20_meta_ads_ops.sql"


def marts_sql(level: InsightsLevel) -> Path:
    return SQL_DIR / f"20_meta_ads_marts_{LEVEL_REPORT[level]}.sql"


def build_pipeline(pipelines_dir: str | None = None, destination: Any = "clickhouse") -> dlt.Pipeline:
    extra: dict[str, Any] = {"pipelines_dir": pipelines_dir} if pipelines_dir else {}
    pipeline: dlt.Pipeline = dlt.pipeline(
        pipeline_name=PIPELINE_NAME, destination=destination, dataset_name=SOURCE_NAME, **extra
    )
    return pipeline


def row_counts(pipeline: dlt.Pipeline) -> dict[str, int]:
    trace = pipeline.last_trace
    if trace is None or trace.last_normalize_info is None:
        return {}
    return dict(trace.last_normalize_info.row_counts)


@dataclass
class RunResult:
    mode: SyncMode
    counts: dict[str, int] = field(default_factory=dict)
    stopped: PacerStop | None = None
    events: list[PacingEvent] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return True  # a pacer stop is a clean end, not a failure


def pacing_events(
    mode: SyncMode, account_ids: Sequence[str], pacer: MetaPacer, stopped: PacerStop | None
) -> list[PacingEvent]:
    """What a run leaves in ops: one row per stop, one summary row when it slept at all."""
    summary = pacer.summary
    accounts = ",".join(account_ids)
    events: list[PacingEvent] = []
    if stopped is not None:
        events.append(
            PacingEvent(
                SOURCE_NAME,
                accounts,
                mode,
                "stopped",
                str(stopped.reason),
                stopped.utilisation,
                summary.total_sleep_seconds,
                stopped.detail,
            )
        )
    elif summary.sleeps:
        events.append(
            PacingEvent(
                SOURCE_NAME,
                accounts,
                mode,
                "slowed",
                "utilisation",
                summary.peak_utilisation,
                summary.total_sleep_seconds,
                f"{summary.sleeps} sleeps over {summary.requests} requests; tier {summary.tier or 'unknown'}",
            )
        )
    return events


def run(
    pipeline: dlt.Pipeline,
    mode: SyncMode,
    account_ids: Sequence[str],
    levels: Sequence[InsightsLevel] = ("campaign",),
    today: date | None = None,
    token_expires_at: date | str | None = None,
    pacer: MetaPacer | None = None,
    apply_sql: bool = True,
    record_ops: bool = True,
    **source_kwargs: Any,
) -> RunResult:
    today = today or date.today()
    pacer = pacer or MetaPacer()
    result = RunResult(mode)

    status = check_token(token_expires_at, today)
    if status.event:
        result.events.append(
            PacingEvent(SOURCE_NAME, ",".join(account_ids), mode, status.event, "token", detail=status.detail)
        )
    if status.expired:
        if record_ops:
            _record(pipeline, result.events, apply_sql)
        raise TokenExpiredError(status)
    if status.expiring:
        log.error("%s: replace it now", status.detail)

    if pipeline.has_pending_data:
        log.warning("loading a pending package from an earlier run first")
        pipeline.load()
    try:
        pipeline.run(
            meta_ads(
                account_ids=list(account_ids),
                mode=mode,
                levels=levels,
                pacer=pacer,
                today=today,
                **source_kwargs,
            )
        )
    except PacerStop as stop:  # raised before any resource ran (for example on the currency lookup)
        log.warning("meta_ads %s ended before loading: %s", mode, stop.detail)
    result.counts = row_counts(pipeline)
    result.stopped = pacer.stopped
    result.events.extend(pacing_events(mode, account_ids, pacer, pacer.stopped))

    if apply_sql:
        for level in levels:
            if result.counts.get(f"insights_{LEVEL_REPORT[level]}", 0) or _table_exists(pipeline, level):
                apply_marts(pipeline, marts_sql(level))
    if record_ops:
        _record(pipeline, result.events, apply_sql)
    return result


def _table_exists(pipeline: dlt.Pipeline, level: InsightsLevel) -> bool:
    with pipeline.sql_client() as client:
        table = f"{SOURCE_NAME}___insights_{LEVEL_REPORT[level]}"
        rows = client.execute_sql(
            "SELECT count() FROM system.tables WHERE database = currentDatabase() AND name = %s", table
        )
    return bool(rows and rows[0][0])


def _record(pipeline: dlt.Pipeline, events: Sequence[PacingEvent], apply_sql: bool) -> None:
    if apply_sql:
        apply_marts(pipeline, OPS_SQL)
    if events:
        with warehouse_client(pipeline) as client:
            record_pacing_events(client, events)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Meta Ads sync")
    parser.add_argument(
        "mode", choices=("daily", "weekly"), help="daily: 7-day re-pull; weekly: 28-day reconcile"
    )
    parser.add_argument(
        "--level", action="append", choices=("campaign", "ad"), help="insights level(s); default campaign"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    account_ids = [str(a) for a in dlt.config["sources.meta_ads.account_ids"]]
    levels: list[InsightsLevel] = args.level or list(
        dlt.config.get("sources.meta_ads.levels", list) or ["campaign"]
    )
    expires_at = dlt.config.get("sources.meta_ads.token_expires_at", str)
    result = run(build_pipeline(), args.mode, account_ids, levels, token_expires_at=expires_at)
    log.info("%s sync loaded %s", args.mode, result.counts)
    if result.stopped is not None:
        log.warning("run ended early by the pacer: %s", result.stopped.detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
