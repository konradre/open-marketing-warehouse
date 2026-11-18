"""Twenty CRM sync entry point: ``daily`` loads everything changed since the last run (``updatedAt``).

uv run python -m omw.pipelines.twenty daily
uv run python -m omw.pipelines.twenty daily --won-stage WON --won-stage RENEWED
uv run python -m omw.pipelines.twenty daily --no-timeline

The closed-won stages (``is_won`` in ``marts.twenty_pipeline``, which the ``won_*`` metrics read) are
configuration: ``sources.twenty.won_stages`` or ``--won-stage``, ``CUSTOMER`` by default. The closed-lost
stages (``is_lost``) are too: ``sources.twenty.lost_stages`` or ``--lost-stage``, none by default.
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

from omw.pipelines.marts import SQL_DIR, apply_marts, raw_table_exists
from omw.sources.twenty import SOURCE_NAME, twenty

log = logging.getLogger(__name__)

PIPELINE_NAME = "twenty"
MARTS_SQL = SQL_DIR / "90_twenty_marts.sql"
TIMELINE_SQL = SQL_DIR / "90_twenty_marts_timeline.sql"
TIMELINE_TABLE = "timeline_activities"
DEFAULT_WON_STAGES: tuple[str, ...] = ("CUSTOMER",)
"""Twenty's default pipeline ends in CUSTOMER. A workspace that renames or adds a closed-won stage sets
``sources.twenty.won_stages``."""
_WON_LIST = re.compile(r"/\*won_stages\*/.*?/\*won_stages\*/")
_WON_COMMENT = re.compile(r"(?<=Closed-won stages here: )[^'\n]*")
_LOST_LIST = re.compile(r"/\*lost_stages\*/.*?/\*lost_stages\*/")
_LOST_COMMENT = re.compile(r"(?<=Closed-lost stages here: )[^'\n]*")
_STAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_ .:/&+-]*")


def _stage_names(configured: Sequence[str], setting: str) -> tuple[str, ...]:
    """Stage names as Twenty stores them, checked. They go into a view definition, so anything that is not
    a plain name is refused rather than escaped."""
    if isinstance(configured, str):
        configured = [configured]
    stages = tuple(dict.fromkeys(str(stage).strip() for stage in configured))
    for stage in stages:
        if not _STAGE.fullmatch(stage):
            raise ValueError(
                f"twenty {setting}: {stage!r} is not a stage name (letters, digits, spaces and _ . : / & + -)"
            )
    return stages


def won_stages(configured: Sequence[str] | None) -> tuple[str, ...]:
    """The closed-won stage names, as Twenty stores them, checked: at least one, each a plain name.

    Twenty stores a select option's value in upper snake case (``CUSTOMER``, ``CLOSED_WON``).
    """
    if configured is None:
        return DEFAULT_WON_STAGES
    stages = _stage_names(configured, "won_stages")
    if not stages:
        raise ValueError("twenty won_stages must name at least one stage (the default is CUSTOMER)")
    return stages


def lost_stages(configured: Sequence[str] | None, won: Sequence[str] = ()) -> tuple[str, ...]:
    """The closed-lost stage names, checked. None by default: Twenty's own pipeline has no lost stage, a
    workspace adds one (``LOST``, ``CLOSED_LOST``). A stage cannot be both won and lost."""
    stages = _stage_names(configured or (), "lost_stages")
    both = [stage for stage in stages if stage in set(won)]
    if both:
        raise ValueError(f"twenty lost_stages: {', '.join(both)} is also a closed-won stage")
    return stages


def render_won_stages(stages: Sequence[str], lost: Sequence[str] = ()) -> Callable[[str], str]:
    """A rewrite of the Twenty marts SQL that puts the closed-won ``stages`` (and the closed-``lost`` ones,
    where a file marks a place for them) where the file carries the default."""
    stages = won_stages(stages)
    lost = lost_stages(lost, stages)
    in_list = ", ".join(f"'{stage}'" for stage in stages)
    lost_list = ", ".join(f"'{stage}'" for stage in lost)

    def render(sql_text: str) -> str:
        sql_text, lists = _WON_LIST.subn(f"/*won_stages*/{in_list}/*won_stages*/", sql_text)
        sql_text, comments = _WON_COMMENT.subn(", ".join(stages), sql_text)
        if not lists or not comments:
            raise ValueError("the Twenty marts SQL has no won_stages marker to configure")
        sql_text = _LOST_LIST.sub(f"/*lost_stages*/{lost_list}/*lost_stages*/", sql_text)
        return _LOST_COMMENT.sub(", ".join(lost) or "none", sql_text)

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
    timeline: bool = True,
    lost: Sequence[str] | None = None,
    **source_kwargs: Any,
) -> dict[str, int]:
    """``won`` names the closed-won stages (default ``CUSTOMER``) and ``lost`` the closed-lost ones (default
    none); they are checked before anything loads.
    ``timeline=False`` leaves Twenty's change feed out of the run; its view is applied whenever the table
    exists, so a later run without the feed still follows a change of ``won``."""
    render = render_won_stages(won_stages(won), lost or ())
    if pipeline.has_pending_data:
        log.warning("loading a pending package from an earlier run first")
        pipeline.load()
    pipeline.run(twenty(timeline=timeline, **source_kwargs))
    counts = row_counts(pipeline)
    if marts_sql is not None:
        apply_marts(pipeline, marts_sql, render)
        if counts.get(TIMELINE_TABLE, 0) or raw_table_exists(pipeline, TIMELINE_TABLE):
            apply_marts(pipeline, TIMELINE_SQL, render)
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
    parser.add_argument(
        "--lost-stage",
        action="append",
        help="a closed-lost stage as Twenty names it (repeatable); "
        "default: sources.twenty.lost_stages, else none",
    )
    parser.add_argument(
        "--no-timeline",
        action="store_true",
        help="leave out timeline activities, Twenty's change feed (also sources.twenty.timeline = false)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    won = won_stages(args.won_stage or dlt.config.get("sources.twenty.won_stages", list))
    configured = dlt.config.get("sources.twenty.timeline", bool)
    timeline = not args.no_timeline and (configured is None or bool(configured))
    lost = lost_stages(args.lost_stage or dlt.config.get("sources.twenty.lost_stages", list), won)
    counts = run_daily(build_pipeline(), won=won, timeline=timeline, lost=lost)
    log.info("closed-won stages: %s; closed-lost stages: %s", ", ".join(won), ", ".join(lost) or "none")
    log.info("daily sync loaded %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
