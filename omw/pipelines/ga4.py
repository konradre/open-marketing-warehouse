"""GA4 sync entry point: ``daily`` re-fetches the last 7 days (``--days N`` for a first load), replacing them.

uv run python -m omw.pipelines.ga4 daily [--days 400]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import dlt

from omw.pipelines.marts import SQL_DIR, apply_marts
from omw.sources.ga4 import RE_PULL_DAYS, SOURCE_NAME, ga4

log = logging.getLogger(__name__)

PIPELINE_NAME = "ga4"
MARTS_SQL = SQL_DIR / "40_ga4_marts.sql"


class EmptyWindowError(RuntimeError):
    """The window loaded no ``core`` rows: a wrong property, a dead key or an empty answer."""


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


def run_daily(
    pipeline: dlt.Pipeline,
    today: date | None = None,
    days: int = RE_PULL_DAYS,
    fail_on_empty: bool = True,
    marts_sql: Path | None = MARTS_SQL,
    **source_kwargs: Any,
) -> dict[str, int]:
    if pipeline.has_pending_data:
        log.warning("loading a pending package from an earlier run first")
        pipeline.load()
    pipeline.run(ga4(today=today, days=days, **source_kwargs))
    counts = row_counts(pipeline)
    if fail_on_empty and counts.get("core", 0) == 0:
        raise EmptyWindowError(
            "the window returned no core rows; check the property ids and the service account"
        )
    if marts_sql is not None:
        apply_marts(pipeline, marts_sql)
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GA4 sync")
    parser.add_argument("mode", choices=("daily",), help="re-fetch the rolling window and replace it")
    parser.add_argument(
        "--days",
        type=int,
        default=RE_PULL_DAYS,
        help=f"days to re-fetch, ending yesterday (default {RE_PULL_DAYS})",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    counts = run_daily(build_pipeline(), days=args.days)
    log.info("daily sync loaded %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
