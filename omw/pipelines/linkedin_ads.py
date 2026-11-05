"""LinkedIn Ads sync entry point: ``daily`` re-fetches the last 7 days and replaces them.

    uv run python -m omw.pipelines.linkedin_ads daily [--days N]

Checks the pinned ``LinkedIn-Version`` first: warns within three months of its sunset, refuses after it.
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
from omw.sources.linkedin_ads import LINKEDIN_VERSION, RE_PULL_DAYS, SOURCE_NAME, check_version, linkedin_ads
from omw.sources.linkedin_ads.version import VersionExpiredError

log = logging.getLogger(__name__)

PIPELINE_NAME = "linkedin_ads"
MARTS_SQL = SQL_DIR / "80_linkedin_ads_marts.sql"


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
    version: str = LINKEDIN_VERSION,
    marts_sql: Path | None = MARTS_SQL,
    **source_kwargs: Any,
) -> dict[str, int]:
    status = check_version(version, today)
    if status.expired:
        raise VersionExpiredError(status)
    if status.expiring:
        log.warning(status.message)
    if pipeline.has_pending_data:
        log.warning("loading a pending package from an earlier run first")
        pipeline.load()
    pipeline.run(linkedin_ads(today=today, days=days, version=version, **source_kwargs))
    counts = row_counts(pipeline)
    if marts_sql is not None:
        apply_marts(pipeline, marts_sql)
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LinkedIn Ads sync")
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
