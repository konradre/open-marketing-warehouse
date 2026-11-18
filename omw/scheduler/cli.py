"""``omw sync``, ``omw schedule`` and ``omw notify``.

omw sync <job>                 one job now: the pipeline, its checks, the run log, the hook; exit 0, 1 or 3
omw schedule run               the scheduler loop (what the scheduler container runs)
omw schedule run --once        run whatever is due and exit; non-zero when any of it failed
omw schedule list              every job, its schedule, its next slot and how its last run ended
omw schedule systemd --output DIR   render the schedule as systemd timer units
omw schedule health            exit 0 while the loop's heartbeat is fresh (the container health check)
omw notify test                send a test alert through every configured channel

``--fixtures DIR`` puts ``sync`` and ``schedule run`` in fixture mode: the same jobs against a directory
written by ``omw demo generate`` instead of the platforms.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import dlt

from omw.pipelines.instants import instant
from omw.scheduler import systemd
from omw.scheduler.jobs import JOB_NAMES, Planned, job_named, read_plan
from omw.scheduler.loop import DEFAULT_TIMEOUT_MINUTES, Scheduler, heartbeat_path, subprocess_launcher
from omw.scheduler.modes import FixtureMode, LiveMode, Mode
from omw.scheduler.notify import Alert, Notifier
from omw.scheduler.runs import RunStore
from omw.scheduler.schedule import ScheduleError
from omw.scheduler.sync import run_job

log = logging.getLogger("omw.scheduler")
SECRETS = Path(".dlt/secrets.toml")
HEALTH_MAX_AGE_SECONDS = 300.0


def mode_of(args: argparse.Namespace) -> Mode:
    return FixtureMode(args.fixtures) if args.fixtures else LiveMode()


def cmd_sync(args: argparse.Namespace) -> int:
    job = job_named(args.job)
    slot = datetime.fromisoformat(args.slot) if args.slot else None
    outcome = run_job(job, mode_of(args), Notifier.from_config(), slot, args.trigger)
    log.info("%s: %s (exit %d)", job.name, outcome.status, outcome.exit_code)
    return outcome.exit_code


def _selected(plan: list[Planned], names: list[str] | None) -> list[Planned]:
    if not names:
        return plan
    for name in names:
        job_named(name)
    return [planned for planned in plan if planned.job.name in names]


def _stop(signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


def cmd_run(args: argparse.Namespace) -> int:
    mode = mode_of(args)
    if mode.name == "live" and SECRETS.exists() and not os.access(SECRETS, os.R_OK):
        log.error(
            "%s is not readable by uid %d: give the scheduler's user read access (docs/DEPLOY.md, Secrets)",
            SECRETS,
            os.getuid(),
        )
        return 1
    plan = _selected(read_plan(), args.job)
    timeout = float(dlt.config.get("scheduler.timeout_minutes", float) or DEFAULT_TIMEOUT_MINUTES)
    heartbeat = heartbeat_path()
    scheduler = Scheduler(
        plan,
        subprocess_launcher(mode.child_args(), timeout, heartbeat),
        lambda: RunStore(mode.client()),
        Notifier.from_config(),
        heartbeat=heartbeat,
    )
    for planned in plan:
        log.info("%-16s %-14s UTC  %s", planned.job.name, planned.schedule, planned.job.summary)
    log.info("scheduler started in %s mode with %d jobs", mode.name, len(plan))
    signal.signal(signal.SIGTERM, _stop)
    try:
        return scheduler.run(once=args.once)
    except KeyboardInterrupt as stop:
        log.info("scheduler stopped (%s)", stop or "interrupt")
        return 0


def cmd_list(args: argparse.Namespace) -> int:
    plan = read_plan()
    try:
        last = RunStore(mode_of(args).client()).last_runs()
    except Exception as exc:  # noqa: BLE001 - the schedule is still worth printing without the warehouse
        log.warning("cannot read the run log: %s", exc)
        last = {}
    now = instant()
    print(f"{'job':<16} {'schedule (UTC)':<30} {'next':<17} {'last run':<17} {'status':<13} detail")
    for planned in plan:
        name = planned.job.name
        ran = last.get(name)
        print(
            f"{name:<16} {planned.schedule!s:<30} {planned.schedule.next(now):%Y-%m-%d %H:%M}  "
            f"{(format(ran.started_at, '%Y-%m-%d %H:%M') if ran else '-'):<17} "
            f"{(ran.status if ran else '-'):<13} {(ran.detail if ran else planned.job.summary)[:100]}"
        )
    for name in JOB_NAMES:
        if name not in {planned.job.name for planned in plan}:
            print(f"{name:<16} off")
    return 0


def cmd_systemd(args: argparse.Namespace) -> int:
    command = args.command or systemd.CONTAINER_COMMAND
    units = systemd.render(read_plan(), args.workdir, command)
    if args.output is None:
        for name, content in units.items():
            print(f"# ---- {name} ----\n{content}")
        return 0
    for path in systemd.write(units, args.output):
        print(path)
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    path = heartbeat_path()
    if not path.exists():
        print(f"no heartbeat at {path}")
        return 1
    age = time.time() - path.stat().st_mtime
    print(f"heartbeat {age:.0f}s ago")
    return 0 if age <= args.max_age else 1


def cmd_notify(args: argparse.Namespace) -> int:
    notifier = Notifier.from_config()
    if not notifier.config.channels:
        log.error("no alert channel is configured: set [alerts] in .dlt/secrets.toml (docs/DEPLOY.md)")
        return 1
    failures = notifier.send(
        Alert("test", "warning", "notify", "test alert from `omw notify test`: the hook works")
    )
    for channel in notifier.config.channels:
        log.info("%s: %s", channel, "failed" if any(f.startswith(channel) for f in failures) else "sent")
    return 1 if failures else 0


def handle(args: argparse.Namespace) -> int:
    try:
        return int(args.scheduler_handler(args))
    except (ScheduleError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 2


def add_parsers(commands: Any) -> None:
    fixtures_help = "fixture mode: read this `omw demo generate` directory instead of the platforms"

    sync = commands.add_parser("sync", help="run one job now, with its checks, run log and failure hook")
    sync.add_argument("job", help=f"one of: {', '.join(JOB_NAMES)}")
    sync.add_argument("--fixtures", type=Path, default=None, help=fixtures_help)
    sync.add_argument(
        "--slot", default=None, help="the schedule slot this run answers (ISO time; set by the loop)"
    )
    sync.add_argument(
        "--trigger", choices=("manual", "schedule", "timer"), default="manual", help="what started the run"
    )
    sync.set_defaults(handler=handle, scheduler_handler=cmd_sync)

    schedule = commands.add_parser(
        "schedule", help="the scheduler: run the loop, list the jobs, render timers"
    )
    actions = schedule.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run", help="start whatever is due, one job at a time, forever")
    run.add_argument("--once", action="store_true", help="run what is due now and exit (non-zero on failure)")
    run.add_argument("--fixtures", type=Path, default=None, help=fixtures_help)
    run.add_argument("--job", action="append", help="only this job (repeatable)")
    run.set_defaults(scheduler_handler=cmd_run)
    listed = actions.add_parser("list", help="every job with its schedule, next slot and last run")
    listed.add_argument("--fixtures", type=Path, default=None, help=fixtures_help)
    listed.set_defaults(scheduler_handler=cmd_list)
    units = actions.add_parser("systemd", help="render the schedule as systemd units")
    units.add_argument("--output", type=Path, default=None, help="directory to write to (default: print)")
    units.add_argument(
        "--workdir", default=systemd.DEFAULT_WORKDIR, help=f"the checkout (default {systemd.DEFAULT_WORKDIR})"
    )
    units.add_argument(
        "--command",
        default=None,
        help="how to call omw (default: in the scheduler image through docker compose); "
        'for a host install: "/usr/local/bin/uv run omw"',
    )
    units.set_defaults(scheduler_handler=cmd_systemd)
    health = actions.add_parser("health", help="exit 0 while the loop's heartbeat is fresh")
    health.add_argument("--max-age", type=float, default=HEALTH_MAX_AGE_SECONDS, help="seconds")
    health.set_defaults(scheduler_handler=cmd_health)
    schedule.set_defaults(handler=handle)

    notify = commands.add_parser("notify", help="the failure hook")
    notify_actions = notify.add_subparsers(dest="action", required=True)
    notify_actions.add_parser("test", help="send a test alert through every configured channel")
    notify.set_defaults(handler=handle, scheduler_handler=cmd_notify)
