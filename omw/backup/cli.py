"""``omw backup``: create, list, verify (the restore drill), restore and prune.

omw backup create [--name NAME.zip]    back up raw, marts and ops to the backups disk
omw backup list                         every backup, its size, its last drill, whether the file is there
omw backup verify [NAME.zip]            restore drill: into scratch databases, compared with the manifest
omw backup restore NAME.zip [--force]   replace raw, marts and ops (the drill runs first)
omw backup prune [--keep N]             delete all but the newest N scheduled backups

``list`` and ``prune`` look at the backup files in ``OMW_BACKUP_DIR`` (``--dir``), which the scheduler
container mounts; run them there (``make backup-list``) when the directory is a Docker volume.
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path
from typing import Any

from dlt.common.pipeline import get_dlt_pipelines_dir

from omw import backup
from omw.pipelines.client import pipeline_client
from omw.scheduler.jobs import SOURCES
from omw.scheduler.sync import backup_dir, backup_keep

log = logging.getLogger("omw.backup")


def forget_local_state(pipelines_dir: Path | None = None) -> list[str]:
    """Remove the local working directories of the seven pipelines.

    dlt keeps each pipeline's state (incremental cursors, backfill progress) locally and in the warehouse,
    and trusts whichever is newer. After a restore the local copy is newer than the restored data, so a
    sync would resume after rows the warehouse no longer holds. Without the local copy dlt takes the
    restored state.
    """
    root = pipelines_dir or Path(get_dlt_pipelines_dir())
    removed = []
    for name in SOURCES:  # every pipeline is named after its source
        path = root / name
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(name)
    return removed


def cmd_create(args: argparse.Namespace) -> int:
    client = pipeline_client()
    made = backup.create(client, args.name)
    print(f"{made.name}: {made.tables} tables and views, {made.rows:,} rows, {made.bytes:,} bytes")
    if args.keep is not None:
        return _prune(args, client)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    directory = args.dir or backup_dir()
    found = backup.listing(pipeline_client(), directory)
    print(
        f"{'backup':<28} {'created (UTC)':<17} {'tables':>6} {'rows':>12} {'bytes':>13}  {'drill':<13} file"
    )
    for item in found:
        print(
            f"{item.name:<28} {item.created_at:%Y-%m-%d %H:%M}  {item.tables:>6} {item.rows:>12,} "
            f"{item.bytes:>13,}  {item.verified or '-':<13} {item.file}"
        )
    if not found:
        print("(no backup yet: `omw backup create`)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    drill = backup.verify(pipeline_client(), args.name)
    if drill.ok:
        print(
            f"{drill.name}: restored into scratch databases, {drill.tables} tables and views, "
            f"{drill.rows:,} rows match"
        )
        return 0
    for problem in drill.problems:
        log.error("%s: %s", drill.name, problem)
    return 1


def cmd_restore(args: argparse.Namespace) -> int:
    drill = backup.restore(pipeline_client(), args.name, args.force)
    print(f"{drill.name}: restored {drill.tables} tables and views, {drill.rows:,} rows")
    forgotten = forget_local_state()
    if forgotten:
        print(
            f"forgot the local dlt state of {', '.join(forgotten)}: "
            "the next sync resumes from the restored state"
        )
    print("next: `omw check`, then start the scheduler again")
    return 0


def _prune(args: argparse.Namespace, client: Any) -> int:
    directory = args.dir or backup_dir()
    if directory is None or not directory.is_dir():
        log.error(
            "the backup directory is not visible from here (OMW_BACKUP_DIR or --dir): run this where it is "
            "mounted, for example `make backup-prune`"
        )
        return 1
    keep = args.keep if args.keep is not None else backup_keep()
    removed = backup.prune(directory, keep, client)
    print(f"kept the newest {keep}; removed {', '.join(removed) if removed else 'nothing'}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    return _prune(args, pipeline_client())


def handle(args: argparse.Namespace) -> int:
    try:
        return int(args.backup_handler(args))
    except backup.BackupError as exc:
        log.error("%s", exc)
        return 1


def add_parser(commands: Any) -> None:
    parser = commands.add_parser("backup", help="back up and restore the warehouse (BACKUP ... TO Disk)")
    actions = parser.add_subparsers(dest="action", required=True)
    directory_help = "where the backup files are visible (default: OMW_BACKUP_DIR)"

    create = actions.add_parser("create", help="back up raw, marts and ops")
    create.add_argument(
        "--name", default=None, help="file name on the backups disk (default: omw-<UTC time>.zip)"
    )
    create.add_argument("--keep", type=int, default=None, help="then prune to the newest N scheduled backups")
    create.add_argument("--dir", type=Path, default=None, help=directory_help)
    create.set_defaults(backup_handler=cmd_create)
    listed = actions.add_parser("list", help="every backup the log knows")
    listed.add_argument("--dir", type=Path, default=None, help=directory_help)
    listed.set_defaults(backup_handler=cmd_list)
    verify = actions.add_parser(
        "verify", help="restore drill into scratch databases; the live data is untouched"
    )
    verify.add_argument("name", nargs="?", default=None, help="backup file name (default: the newest)")
    verify.set_defaults(backup_handler=cmd_verify)
    restore = actions.add_parser("restore", help="replace raw, marts and ops with a backup")
    restore.add_argument("name", help="backup file name")
    restore.add_argument("--force", action="store_true", help="replace the tables and views that are there")
    restore.set_defaults(backup_handler=cmd_restore)
    prune = actions.add_parser("prune", help="delete all but the newest scheduled backups")
    prune.add_argument(
        "--keep", type=int, default=None, help="how many to keep (default: [backup] keep, else 7)"
    )
    prune.add_argument("--dir", type=Path, default=None, help=directory_help)
    prune.set_defaults(backup_handler=cmd_prune)
    parser.set_defaults(handler=handle)
