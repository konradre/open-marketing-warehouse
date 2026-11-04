"""The dlt source end to end on the dummy destination: resources, hints, state and the backfill driver."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import dlt
import pytest

from omw.pipelines.search_console import EmptyWindowError, run_backfill, run_daily, source_state
from omw.sources.search_console import GRAINS, Window, backfill_progress, search_console
from omw.sources.search_console.windows import backfill_chunks
from tests.unit.search_console.conftest import SITE, FakeSearchConsoleClient

TODAY = date(2026, 10, 14)  # daily window = 2026-10-04..13; fixtures cover 2026-10-01..03


def _pipeline(tmp_path: Path) -> dlt.Pipeline:
    return dlt.pipeline(
        pipeline_name="search_console_unit",
        destination=dlt.destinations.dummy(completed_prob=1.0),
        dataset_name="search_console",
        pipelines_dir=str(tmp_path),
    )


def test_source_has_one_resource_per_grain_plus_sync_windows(fake_client: FakeSearchConsoleClient) -> None:
    source = search_console(site_urls=[SITE], client=fake_client, today=TODAY)
    assert set(source.resources) == set(GRAINS) | {"sync_windows"}
    queries = source.resources["queries"]
    hints: dict[str, Any] = dict(queries.compute_table_schema())
    assert hints["write_disposition"] == "merge"
    assert hints["x-merge-strategy"] == "delete-insert"
    assert [c for c, h in hints["columns"].items() if h.get("merge_key")] == [
        "site_url",
        "search_type",
        "date",
    ]
    assert hints["x-clickhouse-sort"] == ["site_url", "search_type", "date", "query"]
    assert all(h["nullable"] is False for h in hints["columns"].values())


def test_rows_fetch_the_window_for_every_property_and_search_type(
    fake_client: FakeSearchConsoleClient,
) -> None:
    window = Window(date(2026, 10, 1), date(2026, 10, 2))
    source = search_console(
        site_urls=[SITE, "https://other.test/"],
        search_types=["web", "image"],
        client=fake_client,
        window=window,
        grains=["core"],
    )
    rows = list(source.resources["core"])
    assert len(rows) == 2 * 2 * 2  # properties x search types x days
    assert {(c["site_url"], c["search_type"]) for c in fake_client.calls} == {
        (SITE, "web"),
        (SITE, "image"),
        ("https://other.test/", "web"),
        ("https://other.test/", "image"),
    }
    assert all(c["start"] == window.start and c["end"] == window.end for c in fake_client.calls)
    assert all(c["dimensions"] == ["date"] for c in fake_client.calls)


def test_unknown_grain_and_missing_credentials_are_rejected(fake_client: FakeSearchConsoleClient) -> None:
    with pytest.raises(ValueError, match="unknown grains"):
        search_console(site_urls=[SITE], client=fake_client, grains=["clicks"])
    with pytest.raises(ValueError, match="credentials_path"):
        search_console(site_urls=[SITE], credentials_path=None)


def test_daily_run_fails_on_an_empty_window(tmp_path: Path, fake_client: FakeSearchConsoleClient) -> None:
    pipeline = _pipeline(tmp_path)
    with pytest.raises(EmptyWindowError):
        run_daily(pipeline, today=TODAY, site_urls=[SITE], client=fake_client, marts_sql=None)


def test_daily_run_loads_the_rolling_window(tmp_path: Path, fake_client: FakeSearchConsoleClient) -> None:
    pipeline = _pipeline(tmp_path)
    counts = run_daily(
        pipeline, today=date(2026, 10, 5), site_urls=[SITE], client=fake_client, marts_sql=None
    )  # window 2026-09-25..10-04 covers the three fixture days
    assert counts["core"] == 3 and counts["devices"] == 6 and counts["sync_windows"] == 1
    assert backfill_progress(source_state(pipeline), SITE, "web") is None  # daily runs do not touch it


def test_backfill_stops_at_the_failed_chunk_and_resumes(
    tmp_path: Path, fake_client: FakeSearchConsoleClient
) -> None:
    pipeline = _pipeline(tmp_path)
    chunks = backfill_chunks(TODAY, None)
    fake_client.fail_windows = {(chunks[2].start, chunks[2].end)}

    first = run_backfill(pipeline, [SITE], today=TODAY, client=fake_client, marts_sql=None, grains=["core"])
    assert not first.ok
    assert [c for _, _, c in first.completed] == chunks[:2]
    assert first.failed == (SITE, "web", chunks[2])
    assert first.remaining == len(chunks) - 2
    assert backfill_progress(source_state(pipeline), SITE, "web") == chunks[1].end

    fake_client.fail_windows = set()
    second = run_backfill(pipeline, [SITE], today=TODAY, client=fake_client, marts_sql=None, grains=["core"])
    assert second.ok
    assert [c for _, _, c in second.completed] == chunks[2:]
    assert second.remaining == 0
    assert backfill_progress(source_state(pipeline), SITE, "web") == chunks[-1].end
    assert run_backfill(pipeline, [SITE], today=TODAY, client=fake_client, marts_sql=None).completed == []


def test_backfill_honours_max_chunks(tmp_path: Path, fake_client: FakeSearchConsoleClient) -> None:
    pipeline = _pipeline(tmp_path)
    result = run_backfill(
        pipeline, [SITE], today=TODAY, max_chunks=1, client=fake_client, marts_sql=None, grains=["core"]
    )
    assert result.ok and len(result.completed) == 1
    assert result.remaining == len(backfill_chunks(TODAY, None)) - 1
