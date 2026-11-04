"""Search Console sync entry points: ``daily`` (rolling 10-day re-pull) and ``backfill`` (16 months,
oldest first, 90-day chunks, resumable).

    uv run python -m omw.pipelines.search_console daily
    uv run python -m omw.pipelines.search_console backfill [--max-chunks N]

Each chunk is one ``pipeline.run``, so a failed chunk leaves the completed ones loaded and the next run
resumes from the dlt state the ``sync_windows`` resource wrote.
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
from dlt.pipeline.exceptions import PipelineStepFailed

from omw.pipelines.marts import SQL_DIR, apply_marts
from omw.sources.search_console import SOURCE_NAME, Window, backfill_chunks, backfill_progress, search_console
from omw.sources.search_console.client import SearchConsoleClient

log = logging.getLogger(__name__)

PIPELINE_NAME = "search_console"
MARTS_SQL = SQL_DIR / "10_search_console_marts.sql"


class EmptyWindowError(RuntimeError):
    """The daily window loaded no ``core`` rows: a dead token, a wrong property or an empty API answer.
    Treated as a failed sync, never as a quiet success."""


def build_pipeline(pipelines_dir: str | None = None, destination: Any = "clickhouse") -> dlt.Pipeline:
    extra: dict[str, Any] = {"pipelines_dir": pipelines_dir} if pipelines_dir else {}
    pipeline: dlt.Pipeline = dlt.pipeline(
        pipeline_name=PIPELINE_NAME, destination=destination, dataset_name=SOURCE_NAME, **extra
    )
    return pipeline


def row_counts(pipeline: dlt.Pipeline) -> dict[str, int]:
    """Rows per table in the last normalize step, or empty when nothing was normalized."""
    trace = pipeline.last_trace
    if trace is None or trace.last_normalize_info is None:
        return {}
    return dict(trace.last_normalize_info.row_counts)


def run_daily(
    pipeline: dlt.Pipeline,
    today: date | None = None,
    fail_on_empty: bool = True,
    marts_sql: Path | None = MARTS_SQL,
    **source_kwargs: Any,
) -> dict[str, int]:
    """Re-fetch the rolling window and replace it. Returns rows loaded per table."""
    if pipeline.has_pending_data:
        log.warning("loading a pending package from an earlier run first")
        pipeline.load()
    pipeline.run(search_console(mode="daily", today=today, **source_kwargs))
    counts = row_counts(pipeline)
    if fail_on_empty and counts.get("core", 0) == 0:
        raise EmptyWindowError("daily window returned no core rows; check the token and the property access")
    if marts_sql is not None:
        apply_marts(pipeline, marts_sql)
    return counts


@dataclass
class BackfillResult:
    completed: list[tuple[str, str, Window]] = field(default_factory=list)
    failed: tuple[str, str, Window] | None = None
    error: Exception | None = None
    remaining: int = 0

    @property
    def ok(self) -> bool:
        return self.failed is None


def source_state(pipeline: dlt.Pipeline) -> dict[str, Any]:
    state: dict[str, Any] = pipeline.state.get("sources", {}).get(SOURCE_NAME, {})
    return state


def run_backfill(
    pipeline: dlt.Pipeline,
    site_urls: Sequence[str],
    search_types: Sequence[str] = ("web",),
    today: date | None = None,
    max_chunks: int | None = None,
    marts_sql: Path | None = MARTS_SQL,
    client: SearchConsoleClient | None = None,
    **source_kwargs: Any,
) -> BackfillResult:
    """Load chunks oldest first until done, ``max_chunks`` is reached, or a chunk fails."""
    today = today or date.today()
    result = BackfillResult()
    if pipeline.has_pending_data:
        log.warning("loading a pending package from an earlier run first")
        pipeline.load()
    plan: list[tuple[str, str, Window]] = []
    for site_url in site_urls:
        for search_type in search_types:
            done = backfill_progress(source_state(pipeline), site_url, search_type)
            plan.extend((site_url, search_type, chunk) for chunk in backfill_chunks(today, done))
    budget = len(plan) if max_chunks is None else min(max_chunks, len(plan))
    for index, (site_url, search_type, chunk) in enumerate(plan[:budget]):
        log.info("backfill %s %s %s (%s of %s)", site_url, search_type, chunk, index + 1, len(plan))
        try:
            pipeline.run(
                search_console(
                    site_urls=[site_url],
                    search_types=[search_type],
                    window=chunk,
                    mode="backfill",
                    client=client,
                    **source_kwargs,
                )
            )
        except (PipelineStepFailed, Exception) as exc:  # noqa: BLE001 - any failure stops the backfill
            log.error("backfill stopped at %s %s %s: %s", site_url, search_type, chunk, exc)
            result.failed = (site_url, search_type, chunk)
            result.error = exc
            result.remaining = len(plan) - index
            return result
        result.completed.append((site_url, search_type, chunk))
    result.remaining = len(plan) - budget
    if result.completed and marts_sql is not None:
        apply_marts(pipeline, marts_sql)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search Console sync")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("daily", help="re-fetch the rolling window and replace it")
    backfill = commands.add_parser("backfill", help="load the 16 months Google keeps, oldest first")
    backfill.add_argument("--max-chunks", type=int, default=None, help="stop after this many 90-day chunks")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    pipeline = build_pipeline()
    if args.command == "daily":
        counts = run_daily(pipeline)
        log.info("daily sync loaded %s", counts)
        return 0
    site_urls = list(dlt.config["sources.search_console.site_urls"])
    search_types = list(dlt.config.get("sources.search_console.search_types", list) or ["web"])
    result = run_backfill(pipeline, site_urls, search_types, max_chunks=args.max_chunks)
    log.info("backfill: %s chunks completed, %s remaining", len(result.completed), result.remaining)
    if not result.ok:
        log.error("backfill failed at %s: %s", result.failed, result.error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
