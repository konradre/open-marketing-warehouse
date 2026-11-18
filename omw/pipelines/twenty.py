"""Twenty CRM sync entry point: ``daily`` loads everything changed since the last run (``updatedAt``).

uv run python -m omw.pipelines.twenty daily
uv run python -m omw.pipelines.twenty daily --won-stage WON --won-stage RENEWED

The closed-won stages (``is_won`` in ``marts.twenty_pipeline``, which the ``won_*`` metrics read) are
configuration: ``sources.twenty.won_stages`` or ``--won-stage``, ``CUSTOMER`` by default.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import dlt

from omw.pipelines.marts import SQL_DIR, apply_marts
from omw.sources.twenty import SOURCE_NAME, twenty

log = logging.getLogger(__name__)

PIPELINE_NAME = "twenty"
MARTS_SQL = SQL_DIR / "90_twenty_marts.sql"
DEFAULT_WON_STAGES: tuple[str, ...] = ("CUSTOMER",)
"""Twenty's default pipeline ends in CUSTOMER. A workspace that renames or adds a closed-won stage sets
``sources.twenty.won_stages``."""
_WON_LIST = re.compile(r"/\*won_stages\*/.*?/\*won_stages\*/")
_WON_COMMENT = re.compile(r"(?<=Closed-won stages here: )[^'\n]*")
_STAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_ .:/&+-]*")


def won_stages(configured: Sequence[str] | None) -> tuple[str, ...]:
    """The closed-won stage names, as Twenty stores them, checked: at least one, each a plain name.

    Twenty stores a select option's value in upper snake case (``CUSTOMER``, ``CLOSED_WON``). The names go
    into a view definition, so anything that is not a plain name is refused rather than escaped.
    """
    if configured is None:
        return DEFAULT_WON_STAGES
    if isinstance(configured, str):
        configured = [configured]
    stages = tuple(dict.fromkeys(str(stage).strip() for stage in configured))
    if not stages:
        raise ValueError("twenty won_stages must name at least one stage (the default is CUSTOMER)")
    for stage in stages:
        if not _STAGE.fullmatch(stage):
            raise ValueError(
                f"twenty won_stages: {stage!r} is not a stage name "
                "(letters, digits, spaces and _ . : / & + -)"
            )
    return stages


def render_won_stages(stages: Sequence[str]) -> Callable[[str], str]:
    """A rewrite of ``90_twenty_marts.sql`` that puts ``stages`` where the file carries the default."""
    stages = won_stages(stages)
    in_list = ", ".join(f"'{stage}'" for stage in stages)

    def render(sql_text: str) -> str:
        sql_text, lists = _WON_LIST.subn(f"/*won_stages*/{in_list}/*won_stages*/", sql_text)
        sql_text, comments = _WON_COMMENT.subn(", ".join(stages), sql_text)
        if not lists or not comments:
            raise ValueError("the Twenty marts SQL has no won_stages marker to configure")
        return sql_text

    return render


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
    marts_sql: Path | None = MARTS_SQL,
    won: Sequence[str] | None = None,
    **source_kwargs: Any,
) -> dict[str, int]:
    """``won`` names the closed-won stages (default ``CUSTOMER``); they are checked before anything loads."""
    render = render_won_stages(won_stages(won))
    if pipeline.has_pending_data:
        log.warning("loading a pending package from an earlier run first")
        pipeline.load()
    pipeline.run(twenty(**source_kwargs))
    counts = row_counts(pipeline)
    if marts_sql is not None:
        apply_marts(pipeline, marts_sql, render)
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Twenty CRM sync")
    parser.add_argument("mode", choices=("daily",), help="load everything changed since the last run")
    parser.add_argument(
        "--won-stage",
        action="append",
        help="a closed-won stage as Twenty names it (repeatable); "
        "default: sources.twenty.won_stages, else CUSTOMER",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    won = won_stages(args.won_stage or dlt.config.get("sources.twenty.won_stages", list))
    counts = run_daily(build_pipeline(), won=won)
    log.info("closed-won stages: %s", ", ".join(won))
    log.info("daily sync loaded %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
