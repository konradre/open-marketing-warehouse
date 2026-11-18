"""The demo's side of the warehouse: where it loads, and whether what is there is its own to replace.

The demo fills the same ``raw`` tables the real pipelines fill. It therefore refuses to touch a warehouse
whose source data it did not load itself. "Its own" is checked, not assumed: after every load the demo
records the latest dlt load of each source in ``ops.demo_load``; when the warehouse no longer matches that
record (a real sync ran, the evaluation was seeded or dropped), the data counts as foreign again.
"""

from __future__ import annotations

from typing import Any

import dlt

from omw.checks.results import Warehouse
from omw.demo.fixtures import SOURCES, Manifest
from omw.eval import seed as eval_seed
from omw.eval.connect import DEV_PASSWORDS, setting

MARKER = "ops.demo_load"


class DemoRefusedError(RuntimeError):
    """The warehouse holds source data the demo did not load."""


def destination() -> Any:
    """dlt's ClickHouse destination as the ``pipeline`` user, from the ``OMW_*`` settings (environment,
    then ``.env``, then the compose dev defaults): the demo needs no ``.dlt/secrets.toml``."""
    return dlt.destinations.clickhouse(
        credentials={
            "host": setting("OMW_CLICKHOUSE_HOST", "127.0.0.1"),
            "port": int(setting("OMW_CLICKHOUSE_NATIVE_PORT", "9000")),
            "http_port": int(setting("OMW_CLICKHOUSE_HTTP_PORT", "8123")),
            "username": "pipeline",
            "password": setting("OMW_PIPELINE_PASSWORD", DEV_PASSWORDS["pipeline"]),
            "database": "raw",
            "secure": 0,
        }
    )


def source_tables(client: Warehouse) -> list[str]:
    """Every ``raw`` table of the seven sources."""
    rows = client.query("SELECT name FROM system.tables WHERE database = 'raw' ORDER BY name").result_rows
    return [str(name) for (name,) in rows if any(str(name).startswith(f"{source}___") for source in SOURCES)]


def row_counts(client: Warehouse) -> dict[str, tuple[int, int]]:
    """source -> (tables, rows) it holds in ``raw``, dlt's own bookkeeping tables left out."""
    found: dict[str, tuple[int, int]] = {}
    for name in source_tables(client):
        source = next(s for s in SOURCES if name.startswith(f"{s}___"))
        if "_dlt_" in name or name.endswith("sync_windows"):
            continue
        rows = int(client.query(f"SELECT count() FROM raw.`{name}`").result_rows[0][0])
        tables, total = found.get(source, (0, 0))
        found[source] = (tables + 1, total + rows)
    return found


def fingerprint(client: Warehouse) -> str:
    """The latest dlt load of every source, as one string. It changes whenever anything loads into ``raw``
    and is empty for a warehouse dlt never loaded."""
    tables = set(source_tables(client))
    found = []
    for source in SOURCES:
        loads = f"{source}____dlt_loads"
        if loads in tables:
            latest = client.query(f"SELECT max(load_id), count() FROM raw.`{loads}`").result_rows[0]
            found.append(f"{source}={latest[0]}/{latest[1]}")
    return " ".join(found)


def recorded(client: Warehouse) -> str | None:
    """The fingerprint the demo recorded after its last load, or ``None`` when it never loaded here."""
    if not client.query(f"EXISTS TABLE {MARKER}").result_rows[0][0]:
        return None
    rows = client.query(f"SELECT argMax(loads, loaded_at) FROM {MARKER}").result_rows
    return str(rows[0][0]) if rows else None


def owned(client: Warehouse) -> bool:
    """True when the source data in the warehouse is exactly what the demo last loaded."""
    return recorded(client) == fingerprint(client)


def reset(client: Warehouse, force: bool = False) -> int:
    """Remove every raw table and marts view of the seven sources, the campaign keys and the check
    results, so the demo starts from an empty warehouse. Refuses when the data is not the demo's own."""
    foreign = source_tables(client)
    if foreign and not owned(client) and not force:
        what = "an evaluation seed" if eval_seed.seeded(client) else "source data the demo did not load"
        raise DemoRefusedError(
            f"the warehouse holds {what} ({len(foreign)} tables, for example raw.{foreign[0]}); "
            "the demo would replace it. Pass --force to do that, or point OMW_CLICKHOUSE_* at another stack"
        )
    dropped = eval_seed.drop(client)
    client.command(f"DROP TABLE IF EXISTS {MARKER}")
    return dropped


def mark(client: Warehouse, manifest: Manifest) -> None:
    """Record what the demo just loaded, so the next run knows the warehouse is its own."""
    client.command(
        f"CREATE TABLE IF NOT EXISTS {MARKER} (seed UInt64, today Date, days UInt32, loads String, "
        "loaded_at DateTime64(3) DEFAULT now64(3)) ENGINE = MergeTree ORDER BY loaded_at"
    )
    client.insert(
        MARKER,
        [[manifest.seed, manifest.today, manifest.days, fingerprint(client)]],
        column_names=["seed", "today", "days", "loads"],
    )
