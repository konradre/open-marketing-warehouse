"""The ``omw`` command. ``omw check`` runs the post-sync checks and exits non-zero on any failure."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from typing import Any

import clickhouse_connect
import dlt

from omw.checks.runner import apply_ops_sql, run_checks

log = logging.getLogger("omw")


def pipeline_client() -> Any:
    """clickhouse-connect as the pipeline user, from the dlt destination credentials."""
    credentials = dlt.secrets["destination.clickhouse.credentials"]
    return clickhouse_connect.get_client(
        host=credentials["host"],
        port=int(credentials.get("http_port", 8123)),
        username=credentials["username"],
        password=credentials["password"],
        secure=bool(credentials.get("secure", 0)),
    )


def check(args: argparse.Namespace) -> int:
    client = pipeline_client()
    apply_ops_sql(client)
    budgets = dlt.config.get("checks.freshness_hours", dict) or None
    run = run_checks(client, budgets=budgets, sources=set(args.source) if args.source else None)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omw", description="open-marketing-warehouse")
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="run the post-sync checks; exit 1 on any failure")
    check_parser.add_argument("--source", action="append", help="only this source (repeatable)")
    check_parser.set_defaults(handler=check)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler: Any = args.handler
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
