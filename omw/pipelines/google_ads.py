"""Google Ads sync entry point: ``daily`` re-fetches the last 30 days and replaces them.

uv run python -m omw.pipelines.google_ads daily
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
from omw.sources.google_ads import SOURCE_NAME, google_ads

log = logging.getLogger(__name__)

PIPELINE_NAME = "google_ads"
MARTS_SQL = SQL_DIR / "30_google_ads_marts.sql"


class EmptyWindowError(RuntimeError):
    """The window loaded no campaign performance rows: a wrong customer, a dead token or an empty answer."""


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
    fail_on_empty: bool = True,
    marts_sql: Path | None = MARTS_SQL,
    **source_kwargs: Any,
) -> dict[str, int]:
    if pipeline.has_pending_data:
        log.warning("loading a pending package from an earlier run first")
        pipeline.load()
    pipeline.run(google_ads(today=today, **source_kwargs))
    counts = row_counts(pipeline)
    if fail_on_empty and counts.get("performance_campaigns", 0) == 0:
        raise EmptyWindowError(
            "the 30-day window returned no campaign rows; check the customer ids and the token"
        )
    if marts_sql is not None:
        apply_marts(pipeline, marts_sql)
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Google Ads sync")
    parser.add_argument("mode", choices=("daily",), help="re-fetch the last 30 days and replace them")
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    counts = run_daily(build_pipeline())
    log.info("daily sync loaded %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
