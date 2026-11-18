"""Create, verify, restore and prune backups. Everything runs as the ``pipeline`` user: it owns the three
databases, and ``sql/00_05_grants.sql`` gives it the scratch databases of the restore drill."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omw.checks.results import Warehouse
from omw.checks.runner import apply_ops_sql
from omw.pipelines.instants import instant
from omw.pipelines.marts import SQL_DIR

log = logging.getLogger(__name__)

DATABASES = ("raw", "marts", "ops")
DISK = "backups"
"""The ClickHouse disk backups go to (deploy/clickhouse/config.d/omw.xml)."""
SCRATCH_PREFIX = "omw_drill_"
"""The restore drill restores ``raw`` as ``omw_drill_raw`` and so on; these names are reserved for it."""
BACKUP_SQL = SQL_DIR / "76_ops_backup.sql"
MANIFEST = "ops.backup_manifest"
BACKUP_LOG = "ops.backup_log"
MANIFEST_COLUMNS = ["backup", "taken_at", "database_name", "table_name", "kind", "rows"]
LOG_COLUMNS = ["at", "backup", "event", "tables", "rows", "bytes", "detail"]
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.zip$")
_SCHEDULED = re.compile(r"^omw-\d{8}T\d{6}Z\.zip$")
"""Names ``create`` gives by default. ``prune`` removes only these, never a backup someone named."""
_SELF_WRITTEN = {("ops", "backup_manifest")}
_BOOKKEEPING = {("ops", "backup_manifest"), ("ops", "backup_log")}
"""Tables ``create`` itself writes between counting and backing up."""


class BackupError(RuntimeError):
    """A backup that could not be made, did not verify, or may not be restored."""


@dataclass(frozen=True)
class Backup:
    name: str
    tables: int
    rows: int
    bytes: int


@dataclass
class Drill:
    name: str
    tables: int = 0
    rows: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class Listed:
    name: str
    created_at: datetime
    tables: int
    rows: int
    bytes: int
    verified: str
    """``verified``, ``verify_failed`` or empty when no drill has run on it."""
    file: str
    """``present``, ``pruned``, ``missing``, or ``unknown`` when the backup directory is not visible from
    here."""


_SERVER_ERROR = re.compile(r"Code: \d+\. DB::\w*Exception: [^\n\"]*")


def brief(exc: Exception, limit: int = 400) -> str:
    """An error in one line: the server's own message when the driver wrapped it in a response body."""
    text = str(exc)
    found = _SERVER_ERROR.search(text)
    line = found.group(0) if found else (text.strip().splitlines() or [exc.__class__.__name__])[0]
    return f"{exc.__class__.__name__}: {line}"[:limit]


def new_name(now: datetime | None = None) -> str:
    return f"omw-{instant(now):%Y%m%dT%H%M%SZ}.zip"


def checked(name: str) -> str:
    """``name`` if it is a plain backup file name. It goes into a SQL string, so anything else is refused."""
    if not _NAME.fullmatch(name):
        raise BackupError(f"{name!r} is not a backup name: letters, digits, . _ - and the .zip ending")
    return name


def _target(name: str) -> str:
    return f"Disk('{DISK}', '{checked(name)}')"


def _ident(database: str, table: str) -> str:
    return f"`{database}`.`{table.replace('`', '``')}`"


def _objects(client: Warehouse, databases: tuple[str, ...]) -> list[tuple[str, str, str]]:
    """(database, name, kind) of every table and view in ``databases``."""
    listed = ", ".join(f"'{database}'" for database in databases)
    rows = client.query(
        f"SELECT database, name, engine FROM system.tables WHERE database IN ({listed}) "
        "ORDER BY database, name"
    ).result_rows
    return [
        (str(db), str(name), "view" if str(engine).endswith("View") else "table") for db, name, engine in rows
    ]


def _count(client: Warehouse, database: str, table: str) -> int:
    return int(client.query(f"SELECT count() FROM {_ident(database, table)}").result_rows[0][0])


def _log(
    client: Warehouse, name: str, event: str, tables: int = 0, rows: int = 0, size: int = 0, detail: str = ""
) -> None:
    client.insert(
        BACKUP_LOG, [[instant(), name, event, tables, rows, size, detail[:2000]]], column_names=LOG_COLUMNS
    )


def _size(client: Warehouse, backup_id: str) -> int:
    """The backup's size as the server reports it; 0 when the pipeline user may not read ``system.backups``
    (a stack bootstrapped before that grant existed: ``make bootstrap``)."""
    try:
        rows = client.query(
            f"SELECT compressed_size FROM system.backups WHERE id = '{backup_id}'"
        ).result_rows
    except Exception as exc:  # noqa: BLE001 - the size is a courtesy
        log.warning("cannot read the backup size (run `make bootstrap` to add the grant): %s", exc)
        return 0
    return int(rows[0][0]) if rows else 0


def create(client: Warehouse, name: str | None = None, now: datetime | None = None) -> Backup:
    """Back up ``raw``, ``marts`` and ``ops`` to one file on the backups disk."""
    name = checked(name or new_name(now))
    apply_ops_sql(client, BACKUP_SQL)
    taken_at = instant(now)
    manifest: list[list[Any]] = []
    for database, table, kind in _objects(client, DATABASES):
        rows = _count(client, database, table) if kind == "table" else 0
        manifest.append([name, taken_at, database, table, kind, rows])
    client.insert(MANIFEST, manifest, column_names=MANIFEST_COLUMNS)
    statement = ", ".join(f"DATABASE {database}" for database in DATABASES)
    try:
        result = client.query(f"BACKUP {statement} TO {_target(name)}").result_rows
    except Exception as exc:
        _log(client, name, "failed", detail=brief(exc))
        raise BackupError(f"the backup {name} failed: {brief(exc)}") from exc
    total = sum(int(row[5]) for row in manifest)
    made = Backup(name, len(manifest), total, _size(client, str(result[0][0])) if result else 0)
    _log(client, name, "created", made.tables, made.rows, made.bytes)
    log.info("backup %s: %d tables and views, %d rows, %d bytes", name, made.tables, made.rows, made.bytes)
    return made


def latest(client: Warehouse) -> str | None:
    """The newest backup the log knows to have been created."""
    apply_ops_sql(client, BACKUP_SQL)
    rows = client.query(
        f"SELECT argMax(backup, at), count() FROM {BACKUP_LOG} WHERE event = 'created'"
    ).result_rows
    return str(rows[0][0]) if rows and rows[0][1] else None


def _drop_scratch(client: Warehouse) -> None:
    for database in DATABASES:
        client.command(f"DROP DATABASE IF EXISTS {SCRATCH_PREFIX}{database} SYNC")


def verify(client: Warehouse, name: str | None = None) -> Drill:
    """The restore drill: restore ``name`` (default: the newest backup) into scratch databases, compare every
    table with the manifest inside the backup (the same row count, and every view present), drop the
    scratch databases again."""
    apply_ops_sql(client, BACKUP_SQL)
    name = name or latest(client)
    if name is None:
        raise BackupError("there is no backup to verify: run `omw backup create` first")
    drill = Drill(checked(name))
    renamed = ", ".join(f"DATABASE {database} AS {SCRATCH_PREFIX}{database}" for database in DATABASES)
    try:
        _drop_scratch(client)
        client.query(f"RESTORE {renamed} FROM {_target(name)}")
        _compare(client, drill)
    except Exception as exc:  # noqa: BLE001 - a backup that cannot be restored is the finding
        drill.problems.append(brief(exc))
    finally:
        try:
            _drop_scratch(client)
        except Exception as exc:  # noqa: BLE001
            drill.problems.append(f"could not drop the scratch databases: {exc}")
    event = "verified" if drill.ok else "verify_failed"
    _log(client, name, event, drill.tables, drill.rows, detail="; ".join(drill.problems))
    log.log(logging.INFO if drill.ok else logging.ERROR, "restore drill of %s: %s", name, event)
    return drill


def _compare(client: Warehouse, drill: Drill) -> None:
    manifest = client.query(
        f"SELECT database_name, table_name, kind, rows FROM {SCRATCH_PREFIX}ops.backup_manifest "
        f"WHERE backup = '{drill.name}' ORDER BY database_name, table_name"
    ).result_rows
    if not manifest:
        drill.problems.append("the backup holds no manifest of its own tables")
        return
    restored = {
        (database.removeprefix(SCRATCH_PREFIX), table)
        for database, table, _kind in _objects(client, tuple(f"{SCRATCH_PREFIX}{d}" for d in DATABASES))
    }
    for database, table, kind, expected in manifest:
        key = (str(database), str(table))
        if key not in restored:
            drill.problems.append(f"{database}.{table} is in the manifest but not in the backup")
            continue
        drill.tables += 1
        scratch = f"{SCRATCH_PREFIX}{database}"
        if kind == "view":
            # Present is all a drill can say of a view: restoring under another database name rewrites its
            # query, and ClickHouse 25.8 drops the table aliases while doing so. A real restore keeps the
            # names, and every view is re-created from sql/ by its pipeline's next sync anyway.
            continue
        found = _count(client, scratch, str(table))
        drill.rows += found
        if key in _SELF_WRITTEN:
            continue
        # ops tables are append-only logs: a check that ran while the backup started only adds rows.
        matches = found >= int(expected) if database == "ops" else found == int(expected)
        if not matches:
            drill.problems.append(f"{database}.{table}: {found} rows restored, the manifest says {expected}")


def restore(client: Warehouse, name: str, force: bool = False) -> Drill:
    """Replace ``raw``, ``marts`` and ``ops`` with the backup ``name``.

    Refuses a warehouse that holds anything unless ``force``. Runs the restore drill first and leaves the
    live data alone when it fails. The caller must stop the scheduler first and forget the local dlt
    pipeline state afterwards (``omw backup restore`` does the second).
    """
    checked(name)
    # The bookkeeping tables are this module's own (an earlier attempt on an empty stack creates them): they
    # are not data a restore could lose.
    existing = [found for found in _objects(client, DATABASES) if found[:2] not in _BOOKKEEPING]
    if existing and not force:
        raise BackupError(
            f"raw, marts and ops hold {len(existing)} tables and views (for example "
            f"{existing[0][0]}.{existing[0][1]}); a restore replaces all of them. Pass --force to do that"
        )
    drill = verify(client, name)
    if not drill.ok:
        raise BackupError(
            f"the restore drill of {name} failed, nothing was replaced: {'; '.join(drill.problems)}"
        )
    current = _objects(client, DATABASES)
    for kind in ("view", "table"):
        for database, table, found in current:
            if found == kind:
                client.command(f"DROP TABLE IF EXISTS {_ident(database, table)} SYNC")
    statement = ", ".join(f"DATABASE {database}" for database in DATABASES)
    client.query(f"RESTORE {statement} FROM {_target(name)}")
    apply_ops_sql(client, BACKUP_SQL)
    _log(
        client,
        name,
        "restored",
        drill.tables,
        drill.rows,
        detail=f"replaced {len(existing)} tables and views",
    )
    log.info("restored %s: %d tables and views, %d rows", name, drill.tables, drill.rows)
    return drill


def prune(directory: Path, keep: int, client: Warehouse | None = None) -> list[str]:
    """Delete all but the newest ``keep`` scheduled backups (``omw-<timestamp>.zip``) in ``directory``.
    Returns the names removed. A backup with any other name is never touched."""
    if keep < 1:
        raise BackupError(f"keep must be at least 1, got {keep}")
    scheduled = sorted(
        path for path in directory.iterdir() if path.is_file() and _SCHEDULED.fullmatch(path.name)
    )
    removed: list[str] = []
    for path in scheduled[:-keep]:
        path.unlink()
        removed.append(path.name)
        if client is not None:
            _log(client, path.name, "pruned")
    return removed


def listing(client: Warehouse, directory: Path | None = None) -> list[Listed]:
    """Every backup that was made, oldest first, with whether its file is still there.

    Read from the manifests, which travel inside the backups: after a restore the list still names the
    backup that was restored, although the log row of its creation was written after it was taken.
    """
    apply_ops_sql(client, BACKUP_SQL)
    made = client.query(
        f"SELECT backup, toUnixTimestamp(min(taken_at)), count(), sum(rows) FROM {MANIFEST} "
        "GROUP BY backup ORDER BY 2, backup"
    ).result_rows
    events = client.query(
        f"SELECT backup, maxIf(bytes, event = 'created'), countIf(event = 'failed'), "
        f"countIf(event != 'failed' AND event != 'pruned'), argMaxIf(event, at, event LIKE 'verif%'), "
        f"countIf(event LIKE 'verif%'), countIf(event = 'pruned') FROM {BACKUP_LOG} GROUP BY backup"
    ).result_rows
    logged = {str(row[0]): row[1:] for row in events}
    visible = directory is not None and directory.is_dir()
    found: list[Listed] = []
    for name, taken, tables, total in made:
        size, failed, succeeded, verified, drills, pruned = logged.get(str(name), (0, 0, 0, "", 0, 0))
        if failed and not succeeded:
            continue  # the BACKUP statement itself failed: there is no file
        file = "pruned" if pruned else "unknown"
        if visible and directory is not None:
            file = "present" if (directory / str(name)).is_file() else file.replace("unknown", "missing")
        found.append(
            Listed(
                str(name),
                datetime.fromtimestamp(int(taken), tz=UTC),
                int(tables),
                int(total),
                int(size),
                str(verified) if drills else "",
                file,
            )
        )
    return found
