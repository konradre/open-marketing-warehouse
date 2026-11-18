"""``omw demo``: a synthetic company, loaded through the real pipelines, then asked five questions.

omw demo run                        everything below, in order (what ``make demo`` runs)
omw demo generate                   write the fixtures: two snapshots of what every platform API answers
omw demo load --snapshot initial    empty the warehouse and load the whole history (refuses foreign data)
omw demo load --snapshot restated   a daily sync: each source re-fetches its window and replaces it
omw demo ask                        five ``query_metrics`` answers: serve, disclose, clarify and refuse
omw demo drop                       remove everything the demo loaded
"""

from __future__ import annotations

import argparse
import logging
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import dlt

from omw.checks.runner import CheckRun, apply_ops_sql, run_checks
from omw.demo import warehouse
from omw.demo.examples import ask, examples, render, table
from omw.demo.fixtures import Manifest, generate
from omw.demo.keys import shared_keys
from omw.demo.load import Run, load_snapshot
from omw.demo.replay import Replay
from omw.demo.restatement import Change, as_rows, compare, observe_all
from omw.demo.world import DEFAULT_DAYS, SEED, Snapshot
from omw.eval.connect import client, mcp_endpoint
from omw.metrics.campaign_keys import set_campaign_key
from omw.metrics.runner import clickhouse_executor
from omw.metrics.schema import load_catalog

log = logging.getLogger("omw.demo")
DEFAULT_OUT = Path("data/demo")


def say(text: str = "") -> None:
    print(text, flush=True)


def heading(text: str) -> None:
    say(f"\n== {text} ==")


def _run(out: Path, manifest: Manifest, snapshot: Snapshot, pipelines_dir: str, first: bool) -> Run:
    replay = Replay(out / snapshot, manifest.data["accounts"])
    return Run(manifest, snapshot, replay, warehouse.destination(), pipelines_dir, first)


def write_fixtures(out: Path, today: date | None, days: int, seed: int) -> Manifest:
    manifest = generate(out, today, days, seed)
    funnel = manifest.data["funnel"]
    say(
        f"{manifest.days} days up to {manifest.today} (seed {manifest.seed}) in {out}: "
        f"{funnel['paying_customers']} paying customers ({funnel['attributed_customers']} attributed to a "
        f"campaign), {sum(funnel['leads'].values())} leads, {funnel['opportunities']} opportunities"
    )
    return manifest


def first_load(out: Path, manifest: Manifest, pipelines_dir: str, force: bool) -> None:
    """Empty the warehouse and load the whole history from the ``initial`` snapshot."""
    pipeline = client("pipeline")
    warehouse.reset(pipeline, force)
    load_snapshot(_run(out, manifest, "initial", pipelines_dir, first=True))
    for source, (tables, rows) in warehouse.row_counts(pipeline).items():
        say(f"  {source:<15} {rows:>6,} rows in {tables} tables")
    for entry in manifest.data["campaign_keys"]:
        for campaign in entry["campaigns"]:
            if campaign["origin"] == "manual":  # what `omw campaign-keys set` does
                set_campaign_key(
                    pipeline,
                    campaign["source"],
                    campaign["campaign_id"],
                    entry["campaign_key"],
                    campaign["campaign_name"],
                )
    warehouse.mark(pipeline, manifest)


def daily_sync(out: Path, manifest: Manifest, pipelines_dir: str, force: bool = False) -> list[Change]:
    """One ordinary daily sync against the ``restated`` snapshot, with what it changed."""
    pipeline = client("pipeline")
    if not warehouse.owned(pipeline) and not force:
        raise warehouse.DemoRefusedError(
            "the warehouse does not hold the demo's first load: run `omw demo load --snapshot initial` first"
        )
    before = observe_all(pipeline)
    load_snapshot(_run(out, manifest, "restated", pipelines_dir, first=False))
    warehouse.mark(pipeline, manifest)
    return compare(before, observe_all(pipeline))


def print_changes(changes: list[Change]) -> None:
    columns, rows = as_rows(changes)
    for line in table(columns, rows):
        say(line)
    say("  Re-fetched days replaced what was there: more days, restated numbers, no row twice.")


def print_keys() -> None:
    columns, rows = shared_keys(client("pipeline"))
    for line in table(columns, rows):
        say(line)
    say("  * set by hand (`omw campaign-keys set`); every other key is the slug of the campaign name.")


def check(manifest: Manifest) -> CheckRun:
    pipeline = client("pipeline")
    apply_ops_sql(pipeline)
    budgets = dlt.config.get("checks.freshness_hours", dict) or None
    run = run_checks(pipeline, budgets=budgets, today=manifest.today)
    for result in run.results:
        if result.status != "pass":
            say(f"  {result.status.upper()} {result.source}/{result.check}/{result.report}: {result.detail}")
    say(f"  {run.summary()} (freshness, over-count and row-count; written to ops.check_results)")
    return run


def answers(manifest: Manifest) -> list[tuple[Any, dict[str, Any]]]:
    """The five examples, asked as the ``agent`` user. Returns (example, answer) pairs."""
    catalog = load_catalog()
    execute = clickhouse_executor(client("agent"))
    asked = examples(manifest.today)
    found = []
    for number, example in enumerate(asked, start=1):
        answer = ask(example, catalog, execute, manifest.today)
        say(render(number, example, answer, len(asked)))
        found.append((example, answer))
    return found


def unexpected(found: list[tuple[Any, dict[str, Any]]]) -> list[str]:
    return [
        f"{example.metrics}: expected {example.expect}, got {answer['mood']} ({answer.get('reason', '')})"
        for example, answer in found
        if answer["mood"] != example.expect
    ]


def cmd_generate(args: argparse.Namespace) -> int:
    write_fixtures(args.out, args.today, args.days, args.seed)
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    manifest = Manifest.read(args.out)
    with tempfile.TemporaryDirectory(prefix="omw-demo-") as pipelines_dir:
        if args.snapshot == "initial":
            first_load(args.out, manifest, pipelines_dir, args.force)
        else:
            print_changes(daily_sync(args.out, manifest, pipelines_dir, args.force))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    problems = unexpected(answers(Manifest.read(args.out)))
    for problem in problems:
        log.error("unexpected answer: %s", problem)
    return 1 if problems else 0


def cmd_drop(args: argparse.Namespace) -> int:
    dropped = warehouse.reset(client("pipeline"), args.force)
    say(f"dropped {dropped} tables and views")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    heading("1/6 Generate: a synthetic company as fixtures, in the shape each platform API answers")
    manifest = write_fixtures(args.out, args.today, args.days, args.seed)
    with tempfile.TemporaryDirectory(prefix="omw-demo-") as pipelines_dir:
        yesterday = manifest.calendar.as_of("initial")
        heading(f"2/6 First load: the real dlt pipelines in fixture mode, as of {yesterday}")
        first_load(args.out, manifest, pipelines_dir, args.force)
        heading(f"3/6 Daily sync, as of {manifest.today}: the platforms have revised their recent days")
        print_changes(daily_sync(args.out, manifest, pipelines_dir))
    heading("4/6 Marts: one campaign key across the ad platforms, GA4, Stripe and Twenty")
    print_keys()
    heading("5/6 Post-sync checks")
    checks = check(manifest)
    heading("6/6 Five questions through query_metrics, as the agent user")
    problems = unexpected(answers(manifest))
    url, _ = mcp_endpoint()
    say(
        f"\nThe warehouse is loaded. Point an agent at {url} (SQL, as the `agent` user) or run "
        "`uv run python -m omw.metrics.server` (metrics). `uv run omw demo drop` removes the demo data."
    )
    for problem in problems:
        log.error("unexpected answer: %s", problem)
    if not checks.ok:
        log.error("post-sync checks failed: %s", checks.summary())
    return 1 if problems or not checks.ok else 0


HANDLERS = {"generate": cmd_generate, "load": cmd_load, "ask": cmd_ask, "drop": cmd_drop, "run": cmd_run}


def handle(args: argparse.Namespace) -> int:
    """Quiet the pipelines' own progress lines unless asked for; the demo prints its own."""
    if not args.verbose:
        logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
    try:
        return HANDLERS[args.action](args)
    except (warehouse.DemoRefusedError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1


def add_parser(commands: Any) -> None:
    parser = commands.add_parser(
        "demo", help="load a synthetic company through the real pipelines and ask it"
    )
    parser.add_argument("--verbose", action="store_true", help="also show the pipelines' own log lines")
    actions = parser.add_subparsers(dest="action", required=True)

    def with_out(name: str, text: str) -> Any:
        sub = actions.add_parser(name, help=text)
        sub.add_argument(
            "--out", type=Path, default=DEFAULT_OUT, help=f"fixture directory (default {DEFAULT_OUT})"
        )
        return sub

    def with_world(sub: Any) -> None:
        sub.add_argument(
            "--days", type=int, default=DEFAULT_DAYS, help=f"days of history (default {DEFAULT_DAYS})"
        )
        sub.add_argument("--seed", type=int, default=SEED, help="every number is a function of it")
        sub.add_argument(
            "--today",
            type=date.fromisoformat,
            default=None,
            help="the day the demo pretends it is (ISO date)",
        )

    force = "replace source data the demo did not load (an evaluation seed, a real sync)"
    run = with_out("run", "generate, load twice, build marts, run the checks and print five answers")
    with_world(run)
    run.add_argument("--force", action="store_true", help=force)
    with_world(with_out("generate", "write the fixture snapshots and the manifest"))
    load = with_out("load", "run the seven pipelines against one snapshot")
    load.add_argument("--snapshot", choices=("initial", "restated"), default="initial")
    load.add_argument("--force", action="store_true", help=force)
    with_out("ask", "print the five example answers from the loaded warehouse")
    drop = actions.add_parser("drop", help="remove everything the demo loaded")
    drop.add_argument("--force", action="store_true", help=force)
    parser.set_defaults(handler=handle)
