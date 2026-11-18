"""The ``omw`` command.

``omw check`` runs the post-sync checks and exits non-zero on any failure. ``omw campaign-keys sync|set|list``
maintains the cross-platform campaign key mapping (``marts.campaign_keys``); ``sync --dry-run`` prints what a
sync would change and writes nothing. ``omw eval seed|run|report|drop``
evaluates the agent read path on a seeded synthetic warehouse (``eval/README.md``). ``omw demo run`` loads a
synthetic company through the real pipelines in fixture mode and prints five example answers (``make demo``).
``omw sync <job>`` runs one scheduled job with its checks and failure hook, ``omw schedule run`` is the
scheduler loop, and ``omw backup create|verify|restore`` backs the warehouse up (``docs/DEPLOY.md``).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from typing import Any

import dlt

from omw.backup.cli import add_parser as add_backup_parser
from omw.checks.expiry import read_expiries, read_warn_days
from omw.checks.runner import apply_ops_sql, run_checks
from omw.demo.cli import add_parser as add_demo_parser
from omw.eval.cli import add_parser as add_eval_parser
from omw.metrics.campaign_keys import (
    Action,
    PlannedKey,
    list_campaign_keys,
    plan_campaign_keys,
    set_campaign_key,
    sync_campaign_keys,
)
from omw.pipelines.client import pipeline_client
from omw.scheduler.cli import add_parsers as add_scheduler_parsers
from omw.scheduler.jobs import SOURCES, enabled_sources, read_plan

log = logging.getLogger("omw")


def checked_sources(budgets: dict[str, float] | None) -> tuple[dict[str, float] | None, set[str]]:
    """The freshness budgets and the sources the expiry check covers, without the sources whose jobs are all
    ``off`` in ``[schedule]``: a source the deployment does not use is neither synced nor checked."""
    enabled = enabled_sources(read_plan())
    if budgets is not None:
        budgets = {source: hours for source, hours in budgets.items() if source in enabled}
    return budgets, set(budgets) if budgets is not None else enabled & set(SOURCES)


def check(args: argparse.Namespace) -> int:
    client = pipeline_client()
    apply_ops_sql(client)
    budgets, sources = checked_sources(dlt.config.get("checks.freshness_hours", dict) or None)
    wanted = set(args.source) if args.source else None
    run = run_checks(
        client,
        budgets=budgets,
        sources=wanted,
        expiries=read_expiries(sources & wanted if wanted else sources),
        expiry_warn_days=read_warn_days(),
    )
    for result in run.results:
        level = {"pass": logging.INFO, "warn": logging.WARNING, "fail": logging.ERROR}[str(result.status)]
        log.log(
            level,
            "%s %s/%s %s: %s",
            result.status.upper(),
            result.source,
            result.check,
            result.report,
            result.detail,
        )
    log.info("checks: %s", run.summary())
    return 0 if run.ok else 1


def print_plan(plan: list[PlannedKey]) -> None:
    """The rows a sync would add or change on stdout, and one count line per source in the log."""
    for row in plan:
        if row.action in (Action.ADD, Action.CHANGE):
            print(
                f"{row.action}\t{row.source}\t{row.native_id}\t{row.current_key or '-'}\t"
                f"{row.campaign_key}\t{row.campaign_name}"
            )
    for source in dict.fromkeys(row.source for row in plan):
        counts = {
            action: sum(1 for r in plan if r.source == source and r.action is action) for action in Action
        }
        log.info(
            "%s (dry run, nothing written): %d to add, %d to change, %d unchanged, "
            "%d kept as set by hand, %d without a usable name",
            source,
            *(counts[action] for action in Action),
        )
    if not plan:
        log.warning("no campaign entities loaded yet; run an ad platform sync first")


def campaign_keys(args: argparse.Namespace) -> int:
    client = pipeline_client()
    if args.action == "sync" and args.dry_run:
        print_plan(plan_campaign_keys(client, sources=set(args.source) if args.source else None))
        return 0
    if args.action == "sync":
        written = sync_campaign_keys(client, sources=set(args.source) if args.source else None)
        for source, count in written.items():
            log.info("%s: %d derived campaign keys", source, count)
        if not written:
            log.warning("no campaign entities loaded yet; run an ad platform sync first")
        return 0
    if args.action == "set":
        set_campaign_key(client, args.source_name, args.native_id, args.campaign_key, args.name or "")
        log.info("%s %s -> %s (manual)", args.source_name, args.native_id, args.campaign_key)
        return 0
    for source, native_id, key, name, origin in list_campaign_keys(client, args.source):
        print(f"{key}\t{source}\t{native_id}\t{origin}\t{name}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omw", description="open-marketing-warehouse")
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="run the post-sync checks; exit 1 on any failure")
    check_parser.add_argument("--source", action="append", help="only this source (repeatable)")
    check_parser.set_defaults(handler=check)

    keys_parser = commands.add_parser("campaign-keys", help="maintain the cross-platform campaign key map")
    actions = keys_parser.add_subparsers(dest="action", required=True)
    sync_parser = actions.add_parser("sync", help="derive a key (the slug of the name) for every campaign")
    sync_parser.add_argument("--source", action="append", help="only this source (repeatable)")
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the keys a sync would add or change (action, source, id, current key, new key, name); "
        "write nothing",
    )
    set_parser = actions.add_parser("set", help="map one campaign by hand; outranks the derived key")
    set_parser.add_argument("source_name", metavar="source", help="meta_ads, google_ads or linkedin_ads")
    set_parser.add_argument("native_id", help="the campaign id as the platform reports it")
    set_parser.add_argument("campaign_key", help="the cross-platform key, for example widgets_q4")
    set_parser.add_argument("--name", help="human-readable campaign name for the key")
    list_parser = actions.add_parser("list", help="print every mapping row")
    list_parser.add_argument("--source", help="only this source")
    keys_parser.set_defaults(handler=campaign_keys)
    add_eval_parser(commands)
    add_demo_parser(commands)
    add_scheduler_parsers(commands)
    add_backup_parser(commands)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler: Any = args.handler
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
