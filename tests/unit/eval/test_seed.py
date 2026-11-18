"""The loader's guard and its DDL, against a warehouse that only records what it is asked."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Any

import pytest

from omw.checks.results import CheckResult, Status
from omw.checks.runner import CheckRun
from omw.eval import seed
from omw.eval.world import NOW, World


class Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class RecordingWarehouse:
    def __init__(self, raw: list[tuple[str, str]], marker: bool) -> None:
        self.raw = raw
        self.marker = marker
        self.commands: list[str] = []
        self.inserts: dict[str, tuple[list[list[Any]], list[str]]] = {}

    def query(self, sql: str) -> Result:
        if sql.startswith("EXISTS TABLE"):
            return Result([(1 if self.marker or "check_results" in sql else 0,)])
        if "database = 'raw'" in sql:
            return Result(self.raw)
        return Result([])

    def command(self, sql: str) -> None:
        self.commands.append(sql)

    def insert(self, table: str, data: list[list[Any]], column_names: list[str]) -> None:
        self.inserts[table] = (data, column_names)


def test_it_refuses_to_replace_source_data_it_did_not_seed(world: World) -> None:
    foreign = RecordingWarehouse(
        [("meta_ads___insights_campaigns", "MergeTree"), ("t1_probe", "MergeTree")], False
    )
    with pytest.raises(seed.SeedRefusedError, match="raw.meta_ads___insights_campaigns"):
        seed.seed(foreign, world)
    assert foreign.commands == [] and foreign.inserts == {}


def test_dropping_takes_the_sources_and_leaves_other_tables_alone() -> None:
    warehouse = RecordingWarehouse([("stripe___invoices", "MergeTree"), ("t1_probe", "MergeTree")], True)
    assert seed.drop(warehouse) == 1
    assert "DROP TABLE IF EXISTS raw.`stripe___invoices`" in warehouse.commands
    assert not any("t1_probe" in command for command in warehouse.commands)
    assert f"DROP TABLE IF EXISTS {seed.MARKER}" in warehouse.commands


def test_raw_tables_are_created_as_dlt_would_and_filled_with_typed_defaults(world: World) -> None:
    warehouse = RecordingWarehouse([], False)
    tables, rows = seed.load_raw(warehouse, world)
    assert tables == 50 and rows == sum(len(found) for found in world.raw.values()) + 7  # one sync row each
    ddl = next(c for c in warehouse.commands if "raw.`meta_ads___campaigns`" in c)
    assert "`stop_time` Nullable(DateTime64(6, 'UTC'))" in ddl and ddl.endswith(
        "ENGINE = MergeTree ORDER BY tuple()"
    )
    data, names = warehouse.inserts["raw.`meta_ads___campaigns`"]
    assert data[0][names.index("stop_time")] is None and data[0][names.index("lifetime_budget")] == ""
    data, names = warehouse.inserts["raw.`stripe___invoices`"]
    unpaid = next(row for row in data if row[names.index("status")] == "open")
    assert unpaid[names.index("paid_at")] == datetime(1970, 1, 1, tzinfo=UTC)
    assert unpaid[names.index("amount_paid")] == 0


def test_the_same_seed_loads_the_same_instants_on_a_host_in_any_time_zone(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The world's datetimes are UTC wall-clock times. A naive datetime is read in the host's zone by the
    driver, so on a host ahead of UTC every timestamp moved, days shifted, and the epoch default stopped
    being the epoch (``deleted_at = 1970-01-01`` matched nothing and the CRM views came back empty)."""
    loads = []
    for zone in ("UTC", "Australia/Perth", "America/Los_Angeles"):
        monkeypatch.setenv("TZ", zone)
        time.tzset()
        warehouse = RecordingWarehouse([], False)
        seed.load_raw(warehouse, world)
        seed.write_campaign_keys(warehouse, world)
        instants = {
            table: [[c.timestamp() if isinstance(c, datetime) else c for c in row] for row in data]
            for table, (data, _) in warehouse.inserts.items()
        }
        naive = [
            table
            for table, (data, _) in warehouse.inserts.items()
            if any(isinstance(c, datetime) and c.tzinfo is None for row in data for c in row)
        ]
        assert naive == [], zone
        loads.append(instants)
    monkeypatch.undo()
    time.tzset()
    assert loads[0] == loads[1] == loads[2]
    opportunities = loads[1]["raw.`twenty___opportunities`"]
    assert any(0.0 in row for row in opportunities)  # the epoch default is the epoch on every host
    stamps = [c for row in loads[1]["marts.campaign_keys"] for c in row if isinstance(c, float)]
    assert stamps and set(stamps) == {NOW.replace(tzinfo=UTC).timestamp()}


@pytest.mark.parametrize(
    ("kind", "value"),
    [("String", ""), ("Int64", 0), ("Decimal(38, 9)", 0), ("Bool", False), ("Date", date(1970, 1, 1)),
     ("DateTime64(6, 'UTC')", datetime(1970, 1, 1)), ("Nullable(String)", None)],
)  # fmt: skip
def test_an_absent_column_gets_its_types_default(kind: str, value: object) -> None:
    assert seed._default(kind) == value


def test_one_source_is_left_stale_on_purpose() -> None:
    fresh = seed._sync_windows("stripe___sync_windows")[0]["loaded_at"]
    stale = seed._sync_windows(f"{seed.STALE_SOURCE}___sync_windows")[0]["loaded_at"]
    assert (NOW - fresh).total_seconds() / 3600 == 2 and (
        NOW - stale
    ).total_seconds() / 3600 == seed.STALE_HOURS

    def result(source: str, check: str, status: Status) -> CheckResult:
        return CheckResult(source, check, "sync", status, 0.0, 0.0, "detail")

    expected = CheckRun([result("ga4", "freshness", Status.FAIL), result("stripe", "freshness", Status.PASS)])
    assert seed.unexpected_verdicts(expected) == []
    surprising = CheckRun(
        [result("ga4", "freshness", Status.PASS), result("meta_ads", "overcount", Status.FAIL)]
    )
    assert len(seed.unexpected_verdicts(surprising)) == 2
