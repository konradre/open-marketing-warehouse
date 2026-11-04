"""Search Console fixtures loaded through dlt into ClickHouse as `pipeline`, read back as `agent`."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import dlt
import pytest
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from omw.pipelines.search_console import run_backfill, run_daily, source_state
from omw.sources.search_console import GRAINS, backfill_progress
from omw.sources.search_console.windows import backfill_chunks
from tests.integration.conftest import setting
from tests.unit.search_console.conftest import SITE, FakeSearchConsoleClient, fixture_rows

pytestmark = pytest.mark.integration

DATASET = "search_console"
TODAY = date(2026, 10, 5)  # daily window 2026-09-25..10-04 covers the fixture days 10-01..03


def _destination() -> dlt.Destination:  # type: ignore[type-arg]
    return dlt.destinations.clickhouse(
        credentials={
            "host": setting("OMW_CLICKHOUSE_HOST"),
            "port": int(setting("OMW_CLICKHOUSE_NATIVE_PORT")),
            "http_port": int(setting("OMW_CLICKHOUSE_HTTP_PORT")),
            "username": "pipeline",
            "password": setting("OMW_PIPELINE_PASSWORD"),
            "database": "raw",
            "secure": 0,
        }
    )


def _drop_everything(admin_client: Client) -> None:
    tables = admin_client.query(
        "SELECT database, name, engine FROM system.tables WHERE (database = 'raw' AND name LIKE {p:String}) "
        "OR (database = 'marts' AND name LIKE {p:String})",
        parameters={"p": f"{DATASET}%"},
    ).result_rows
    for database, name, engine in tables:
        kind = "VIEW" if engine == "View" else "TABLE"
        admin_client.command(f"DROP {kind} IF EXISTS {database}.`{name}`")


@pytest.fixture(scope="module")
def loaded(
    admin_client: Client, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[dlt.Pipeline, FakeSearchConsoleClient]]:
    _drop_everything(admin_client)
    pipeline = dlt.pipeline(
        pipeline_name="search_console_it",
        destination=_destination(),
        dataset_name=DATASET,
        pipelines_dir=str(tmp_path_factory.mktemp("dlt")),
    )
    fake = FakeSearchConsoleClient()
    counts = run_daily(pipeline, today=TODAY, site_urls=[SITE], client=fake)
    assert counts["core"] == 3
    yield pipeline, fake
    _drop_everything(admin_client)


def test_raw_tables_follow_the_clickhouse_conventions(
    loaded: tuple[dlt.Pipeline, FakeSearchConsoleClient], admin_client: Client
) -> None:
    for name, grain in GRAINS.items():
        sorting_key = admin_client.query(
            "SELECT sorting_key FROM system.tables WHERE database = 'raw' AND name = {t:String}",
            parameters={"t": f"{DATASET}___{name}"},
        ).result_rows[0][0]
        assert sorting_key == ", ".join(grain.sort_key), name
        types = admin_client.query(
            "SELECT name, type FROM system.columns WHERE database = 'raw' AND table = {t:String}",
            parameters={"t": f"{DATASET}___{name}"},
        ).result_rows
        assert not [c for c, t in types if "Nullable" in t and not c.startswith("_dlt")], types


def test_agent_reads_every_grain_through_commented_views(
    loaded: tuple[dlt.Pipeline, FakeSearchConsoleClient], agent_client: Client
) -> None:
    for name in GRAINS:
        view = f"marts.{DATASET}_{name}"
        rows = agent_client.query(f"SELECT count(), uniqExact(report) FROM {view}").result_rows[0]
        assert rows[0] == len(fixture_rows(name)) and rows[1] == 1, view
        columns = agent_client.query(
            "SELECT name, type, comment FROM system.columns WHERE database = 'marts' AND table = {t:String}",
            parameters={"t": f"{DATASET}_{name}"},
        ).result_rows
        assert columns, view
        assert all(comment for _, _, comment in columns), [c for c, _, comment in columns if not comment]
        by_name = {c: t for c, t, _ in columns}
        assert by_name["site_url"] == "LowCardinality(String)"
        assert by_name["search_type"] == "LowCardinality(String)"
        assert by_name["date"] == "Date"
        assert by_name["clicks"] == "UInt64"


def test_raw_stays_invisible_to_the_agent(
    loaded: tuple[dlt.Pipeline, FakeSearchConsoleClient], agent_client: Client
) -> None:
    with pytest.raises(DatabaseError, match="Code: 497"):
        agent_client.query(f"SELECT count() FROM raw.{DATASET}___core")
    assert "raw" not in {r[0] for r in agent_client.query("SELECT database FROM system.tables").result_rows}


def test_breakdowns_never_overcount_core_in_marts(
    loaded: tuple[dlt.Pipeline, FakeSearchConsoleClient], agent_client: Client
) -> None:
    core = dict(
        agent_client.query(f"SELECT date, sum(clicks) FROM marts.{DATASET}_core GROUP BY date").result_rows
    )
    for name in ("queries", "pages", "query_page", "countries", "devices"):
        for day, clicks in agent_client.query(
            f"SELECT date, sum(clicks) FROM marts.{DATASET}_{name} GROUP BY date"
        ).result_rows:
            assert clicks <= core[day], (name, day)
    devices = dict(
        agent_client.query(f"SELECT date, sum(clicks) FROM marts.{DATASET}_devices GROUP BY date").result_rows
    )
    assert devices == core


def test_restated_day_is_replaced_not_duplicated(
    loaded: tuple[dlt.Pipeline, FakeSearchConsoleClient], agent_client: Client
) -> None:
    pipeline, fake = loaded
    before = agent_client.query(
        f"SELECT clicks FROM marts.{DATASET}_core WHERE date = '2026-10-02'"
    ).result_rows
    assert before == [(120,)]
    fake.restated["core"] = fixture_rows("core_restated_2026-10-02")
    run_daily(pipeline, today=TODAY, site_urls=[SITE], client=fake)
    rows = agent_client.query(f"SELECT date, clicks FROM marts.{DATASET}_core ORDER BY date").result_rows
    assert rows == [(date(2026, 10, 1), 100), (date(2026, 10, 2), 135), (date(2026, 10, 3), 90)]
    loads = agent_client.query(f"SELECT count() FROM marts.{DATASET}_devices").result_rows[0][0]
    assert loads == len(fixture_rows("devices"))


def test_sync_windows_are_logged(
    loaded: tuple[dlt.Pipeline, FakeSearchConsoleClient], admin_client: Client
) -> None:
    rows = admin_client.query(
        f"SELECT site_url, search_type, mode, window_start, window_end FROM raw.{DATASET}___sync_windows "
        "ORDER BY loaded_at LIMIT 1"
    ).result_rows
    assert rows == [(SITE, "web", "daily", date(2026, 9, 25), date(2026, 10, 4))]


def test_backfill_progress_is_restored_from_the_warehouse(
    loaded: tuple[dlt.Pipeline, FakeSearchConsoleClient], tmp_path: Path
) -> None:
    pipeline, fake = loaded
    chunks = backfill_chunks(TODAY, None)
    result = run_backfill(pipeline, [SITE], today=TODAY, max_chunks=1, client=fake, grains=["core"])
    assert result.ok and [c for _, _, c in result.completed] == chunks[:1]
    assert backfill_progress(source_state(pipeline), SITE, "web") == chunks[0].end

    fresh = dlt.pipeline(
        pipeline_name="search_console_it",
        destination=_destination(),
        dataset_name=DATASET,
        pipelines_dir=str(tmp_path),
    )
    fresh.sync_destination()
    assert backfill_progress(source_state(fresh), SITE, "web") == chunks[0].end
