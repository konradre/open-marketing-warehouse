"""Load the synthetic world into ClickHouse the way a pipeline run would leave it.

The seed writes ``raw`` only. Everything the agent reads is then produced by the repo's own code: the
``marts`` views from ``sql/``, the campaign keys from ``omw.metrics.campaign_keys`` and the check verdicts
from ``omw.checks``. So the evaluation exercises the real read path, not a stand-in for it.

Seeding **replaces** the raw tables and marts views of all seven sources. It refuses to touch a warehouse
that holds source data it did not seed itself unless told to (``force``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from omw.checks.results import Status, Warehouse
from omw.checks.runner import CheckRun, apply_ops_sql, run_checks
from omw.eval.raw_schema import RAW_TABLES, columns, source_of
from omw.eval.world import END, NOW, START, TODAY, World
from omw.metrics.campaign_keys import CAMPAIGN_KEYS, COLUMNS, sync_campaign_keys
from omw.pipelines.marts import SQL_DIR, statements

log = logging.getLogger(__name__)

SOURCES = ("search_console", "meta_ads", "google_ads", "ga4", "stripe", "linkedin_ads", "twenty")
MARKER = "ops.eval_seed"
STALE_SOURCE = "ga4"
"""The one source the seed leaves past its freshness budget, so its answers have to carry that caveat."""
STALE_HOURS = 80
EPOCH_TYPES = ("DateTime", "Date")


class SeedRefusedError(RuntimeError):
    """The warehouse holds source data this seed did not put there."""


@dataclass
class SeedReport:
    tables: int
    rows: int
    views: int
    checks: CheckRun


def _existing(client: Warehouse, database: str) -> dict[str, str]:
    """name -> engine for every table and view of the seven sources in ``database``."""
    rows = client.query(
        f"SELECT name, engine FROM system.tables WHERE database = '{database}'"  # noqa: S608
    ).result_rows
    return {
        str(name): str(engine)
        for name, engine in rows
        if any(str(name).startswith(f"{source}_") for source in SOURCES)
    }


def seeded(client: Warehouse) -> bool:
    return bool(client.query(f"EXISTS TABLE {MARKER}").result_rows[0][0])


def drop(client: Warehouse) -> int:
    """Remove every raw table and marts view of the seven sources, the campaign keys, their check results
    and the marker. Returns how many objects went."""
    dropped = 0
    for database in ("marts", "raw"):
        for name, engine in _existing(client, database).items():
            client.command(f"DROP {'VIEW' if engine == 'View' else 'TABLE'} IF EXISTS {database}.`{name}`")
            dropped += 1
    client.command(f"DROP TABLE IF EXISTS {CAMPAIGN_KEYS}")
    if client.query("EXISTS TABLE ops.check_results").result_rows[0][0]:
        client.command("TRUNCATE TABLE ops.check_results")
    client.command(f"DROP TABLE IF EXISTS {MARKER}")
    return dropped


def _default(kind: str) -> Any:
    """The value an absent column gets: the type's own default, as dlt writes for a missing field."""
    if kind.startswith("Nullable"):
        return None
    if kind.startswith("DateTime"):
        return datetime(1970, 1, 1)
    if kind == "Date":
        return date(1970, 1, 1)
    if kind == "Bool":
        return False
    if kind.startswith(("Int", "UInt", "Float", "Decimal")):
        return 0
    return ""


def _utc(value: Any) -> Any:
    """A generated datetime as an instant. The world's datetimes are naive UTC wall-clock times; the driver
    would read a naive one in the host's time zone, and a host that is not on UTC would load every
    timestamp shifted (and every epoch default as a real date, hiding rows that test for it)."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _sync_windows(table: str) -> list[dict[str, Any]]:
    """One completed daily run per source, as its ``sync_windows`` log records it."""
    source = source_of(table)
    age = timedelta(hours=STALE_HOURS if source == STALE_SOURCE else 2)
    return [
        {
            "mode": "daily",
            "window_start": END - timedelta(days=9),
            "window_end": END,
            "invoices_since": datetime.combine(START, datetime.min.time()),
            "loaded_at": NOW - age,
        }
    ]


def load_raw(client: Warehouse, world: World) -> tuple[int, int]:
    """Create every raw table and insert the world's rows. Returns (tables, rows)."""
    total = 0
    for table in RAW_TABLES:
        spec = columns(table)
        ddl = ", ".join(f"`{name}` {kind}" for name, kind in spec)
        client.command(f"CREATE TABLE raw.`{table}` ({ddl}) ENGINE = MergeTree ORDER BY tuple()")
        rows = world.raw.get(table) or (_sync_windows(table) if table.endswith("sync_windows") else [])
        if not rows:
            continue
        data = [[_utc(row.get(name, _default(kind))) for name, kind in spec] for row in rows]
        client.insert(f"raw.`{table}`", data, column_names=[name for name, _ in spec])
        total += len(data)
    return len(RAW_TABLES), total


def apply_views(client: Warehouse) -> int:
    """Every ``sql/NN_*.sql`` after the bootstrap (``00_*``), in file order: marts views, the campaign key
    table, the ops objects."""
    applied = 0
    for path in sorted(SQL_DIR.glob("[1-9]*.sql")):
        for statement in statements(path.read_text()):
            client.command(statement)
            applied += 1
    return applied


def write_campaign_keys(client: Warehouse, world: World) -> None:
    """Derived keys through the pipeline's own sync, then the hand-set rows, which outrank them."""
    sync_campaign_keys(client, now=_utc(NOW - timedelta(hours=2)))
    manual = [
        [row["source"], row["native_id"], row["campaign_key"], row["campaign_name"], "manual", _utc(NOW)]
        for row in world.manual_keys
    ]
    if manual:
        client.insert(CAMPAIGN_KEYS, manual, column_names=COLUMNS)


def seed(client: Warehouse, world: World, force: bool = False) -> SeedReport:
    """Replace the warehouse's source data with ``world``. ``client`` is the pipeline user."""
    foreign = _existing(client, "raw")
    if foreign and not seeded(client) and not force:
        raise SeedRefusedError(
            f"raw holds {len(foreign)} source tables this seed did not create (for example "
            f"raw.{sorted(foreign)[0]}); seeding would replace them. Pass force to do it anyway."
        )
    drop(client)
    tables, rows = load_raw(client, world)
    views = apply_views(client)
    write_campaign_keys(client, world)
    apply_ops_sql(client)
    checks = run_checks(client, now=NOW, today=TODAY)
    client.command(
        f"CREATE TABLE {MARKER} (seed UInt64, today Date, tables UInt32, rows UInt64, seeded_at DateTime "
        "DEFAULT now()) ENGINE = MergeTree ORDER BY seed"
    )
    client.insert(
        MARKER, [[world.seed, TODAY, tables, rows]], column_names=["seed", "today", "tables", "rows"]
    )
    log.info("seeded %d rows into %d raw tables, %d statements applied", rows, tables, views)
    return SeedReport(tables, rows, views, checks)


def unexpected_verdicts(checks: CheckRun) -> list[str]:
    """Check results that are not what the seed is built to produce: everything passes except the stale
    source's freshness."""
    found = []
    for result in checks.results:
        expected = (
            Status.FAIL if (result.source, result.check) == (STALE_SOURCE, "freshness") else Status.PASS
        )
        if result.status is not expected:
            found.append(f"{result.source}/{result.check}/{result.report}: {result.status} ({result.detail})")
    return found
