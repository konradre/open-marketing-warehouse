"""Maintain ``marts.campaign_keys`` from the campaign entities every ad pipeline loads (DESIGN.md §6).

The default key of a campaign is the **slug of its name**: lower case, runs of anything but letters and
digits collapsed to one underscore. Campaigns that carry the same name on two platforms therefore share a
key without any manual work, and Stripe or Twenty attribution metadata only has to carry that slug. Rows
written here have ``origin = 'derived'``; rows set by hand (``omw campaign-keys set``) have
``origin = 'manual'`` and are never overwritten by a sync. ``plan_campaign_keys`` is the same decision
without the write (``omw campaign-keys sync --dry-run``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import dlt

from omw.checks.results import Warehouse
from omw.pipelines.instants import instant
from omw.pipelines.marts import SQL_DIR, statements
from omw.pipelines.ops_log import warehouse_client

CAMPAIGN_KEYS = "marts.campaign_keys"
METRIC_LAYER_SQL = SQL_DIR / "60_metric_layer.sql"
COLUMNS = ["source", "native_id", "campaign_key", "campaign_name", "origin", "updated_at"]
_NON_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class CampaignTable:
    source: str
    table: str
    id_column: str
    name_column: str


CAMPAIGN_TABLES: tuple[CampaignTable, ...] = (
    CampaignTable("meta_ads", "raw.meta_ads___campaigns", "id", "name"),
    CampaignTable("google_ads", "raw.google_ads___campaigns", "campaign_id", "campaign_name"),
    CampaignTable("linkedin_ads", "raw.linkedin_ads___campaigns", "id", "name"),
)


def slug(name: str) -> str:
    """``"Widgets - Prospecting"`` -> ``widgets_prospecting``; empty names stay empty."""
    return _NON_WORD.sub("_", name.lower()).strip("_")


def ensure_table(client: Warehouse) -> None:
    """Create or migrate ``marts.campaign_keys`` (idempotent)."""
    for statement in statements(METRIC_LAYER_SQL.read_text()):
        client.command(statement)


def existing_tables(client: Warehouse) -> set[str]:
    rows = client.query(
        "SELECT database, name FROM system.tables WHERE database IN ('raw', 'marts')"
    ).result_rows
    return {f"{database}.{name}" for database, name in rows}


def manual_keys(client: Warehouse) -> set[tuple[str, str]]:
    rows = client.query(
        f"SELECT source, native_id FROM {CAMPAIGN_KEYS} FINAL WHERE origin = 'manual'"
    ).result_rows
    return {(str(source), str(native_id)) for source, native_id in rows}


def loaded_campaigns(client: Warehouse, table: CampaignTable) -> list[tuple[str, str]]:
    """``(native id, name)`` of every campaign the source's entity table holds."""
    rows = client.query(
        f"SELECT toString({table.id_column}), {table.name_column} FROM {table.table} "
        f"WHERE {table.id_column} != ''"
    ).result_rows
    return [(str(native_id), str(name or "")) for native_id, name in rows]


def derived_rows(
    client: Warehouse, table: CampaignTable, protected: set[tuple[str, str]], now: datetime
) -> list[list[Any]]:
    out: list[list[Any]] = []
    for native_id, name in loaded_campaigns(client, table):
        if (table.source, native_id) in protected or not slug(name):
            continue
        out.append([table.source, native_id, slug(name), name, "derived", now])
    return out


def sync_campaign_keys(
    client: Warehouse,
    sources: set[str] | None = None,
    now: datetime | None = None,
    ensure: bool = True,
) -> dict[str, int]:
    """Write a derived key for every campaign of ``sources`` (default: every loaded ad platform).

    Returns rows written per source. Sources whose entity table is not loaded yet are skipped.
    """
    if ensure:
        ensure_table(client)
    now = instant(now)
    loaded = existing_tables(client)
    protected = manual_keys(client)
    written: dict[str, int] = {}
    for table in CAMPAIGN_TABLES:
        if sources is not None and table.source not in sources:
            continue
        if table.table not in loaded:
            continue
        rows = derived_rows(client, table, protected, now)
        if rows:
            client.insert(CAMPAIGN_KEYS, rows, column_names=COLUMNS)
        written[table.source] = len(rows)
    return written


class Action(StrEnum):
    """What a sync would do with one campaign."""

    ADD = "add"  # no mapping row yet: the derived key is new
    CHANGE = "change"  # a derived row exists with another key or name (the campaign was renamed)
    UNCHANGED = "unchanged"  # the derived row already says the same; a sync only refreshes its timestamp
    MANUAL = "manual"  # a row set by hand protects the campaign; a sync writes nothing
    NO_KEY = "no_key"  # the name has no letter or digit; no row, the `<source>:<id>` fallback applies


WRITTEN = frozenset({Action.ADD, Action.CHANGE, Action.UNCHANGED})
"""The actions a sync writes a row for."""


@dataclass(frozen=True)
class PlannedKey:
    source: str
    native_id: str
    campaign_name: str
    campaign_key: str
    """The key a sync would derive (empty for ``no_key``)."""
    current_key: str
    """The key the mapping holds now (empty when it has no row for the campaign)."""
    action: Action


def plan_campaign_keys(client: Warehouse, sources: set[str] | None = None) -> list[PlannedKey]:
    """What ``sync_campaign_keys`` would do for every loaded campaign of ``sources``, without writing.

    Read-only: it neither creates nor migrates ``marts.campaign_keys``. When the table does not exist yet
    every campaign with a usable name is an ``add``.
    """
    loaded = existing_tables(client)
    current: dict[tuple[str, str], tuple[str, str, str]] = {}
    if CAMPAIGN_KEYS in loaded:
        for source, native_id, key, name, origin in list_campaign_keys(client):
            current[(str(source), str(native_id))] = (str(key), str(name), str(origin))
    plan: list[PlannedKey] = []
    for table in CAMPAIGN_TABLES:
        if (sources is not None and table.source not in sources) or table.table not in loaded:
            continue
        for native_id, name in sorted(loaded_campaigns(client, table)):
            key = slug(name)
            now_key, now_name, origin = current.get((table.source, native_id), ("", "", ""))
            if origin == "manual":
                action = Action.MANUAL
            elif not key:
                action = Action.NO_KEY
            elif not origin:
                action = Action.ADD
            elif (now_key, now_name) != (key, name):
                action = Action.CHANGE
            else:
                action = Action.UNCHANGED
            plan.append(PlannedKey(table.source, native_id, name, key, now_key, action))
    return plan


def set_campaign_key(
    client: Warehouse, source: str, native_id: str, campaign_key: str, campaign_name: str = ""
) -> None:
    """A manual mapping row. It outranks any derived row and survives every later sync."""
    if not campaign_key.strip():
        raise ValueError("campaign_key must not be empty")
    client.insert(
        CAMPAIGN_KEYS,
        [[source, native_id, campaign_key, campaign_name, "manual", instant()]],
        column_names=COLUMNS,
    )


def list_campaign_keys(client: Warehouse, source: str | None = None) -> list[list[Any]]:
    where = f" WHERE source = '{source}'" if source else ""
    return [
        list(row)
        for row in client.query(
            f"SELECT source, native_id, campaign_key, campaign_name, origin "
            f"FROM {CAMPAIGN_KEYS} FINAL{where} ORDER BY campaign_key, source, native_id"
        ).result_rows
    ]


def sync_after_load(pipeline: dlt.Pipeline, source: str) -> dict[str, int]:
    """Called by the ad pipelines after their marts: derive keys for ``source`` on a ClickHouse destination.

    Other destinations (the dummy one in unit tests) have no warehouse to write to and are skipped.
    """
    if pipeline.destination.destination_name != "clickhouse":
        return {}
    with warehouse_client(pipeline) as client:
        return sync_campaign_keys(client, sources={source})
