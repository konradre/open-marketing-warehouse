"""GA4: client paging and errors, grains and the (other) row, source hints, pipeline on dummy."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import dlt
import pytest
import responses

from omw.pipelines.ga4 import EmptyWindowError, run_daily
from omw.sources.ga4 import GRAINS, METRICS, OTHER, GA4AccessError, GA4Client, ga4, shape_row
from omw.sources.ga4.client import default_session
from omw.sources.ga4.grains import column_hints
from omw.sources.windows import Window
from tests.unit.ga4.conftest import FIXTURES, PROPERTY, FakeGA4Client, fixture_rows

URL = GA4Client.report_url(PROPERTY)
WINDOW = Window(date(2026, 10, 1), date(2026, 10, 3))


def _client(limit: int = 250_000) -> GA4Client:
    return GA4Client(
        lambda: "tok", session=default_session(max_attempts=3, backoff_factor=0.0, timeout=5), limit=limit
    )


def _body(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


def test_report_url() -> None:
    assert URL == "https://analyticsdata.googleapis.com/v1beta/properties/123456789:runReport"
    assert GA4Client.report_url("properties/123456789") == URL


@responses.activate
def test_pages_with_offset_until_row_count() -> None:
    responses.post(URL, body=_body("page_1"), content_type="application/json")
    responses.post(URL, body=_body("page_2"), content_type="application/json")
    rows = list(
        _client(limit=2).run_report(
            PROPERTY, WINDOW.start, WINDOW.end, ["date", "sessionDefaultChannelGroup"], METRICS
        )
    )
    assert len(rows) == 3
    bodies = [json.loads(c.request.body or "") for c in responses.calls]
    assert [b["offset"] for b in bodies] == [0, 2]
    assert bodies[0]["dateRanges"] == [{"startDate": "2026-10-01", "endDate": "2026-10-03"}]
    assert bodies[0]["dimensions"] == [{"name": "date"}, {"name": "sessionDefaultChannelGroup"}]
    assert [m["name"] for m in bodies[0]["metrics"]] == list(METRICS)
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"


@responses.activate
def test_empty_report_and_errors() -> None:
    responses.post(URL, body=_body("empty"), content_type="application/json")
    assert list(_client().run_report(PROPERTY, WINDOW.start, WINDOW.end, ["date"], METRICS)) == []
    responses.post(URL, body=_body("forbidden"), status=403, content_type="application/json")
    with pytest.raises(GA4AccessError, match="123456789"):
        list(_client().run_report(PROPERTY, WINDOW.start, WINDOW.end, ["date"], METRICS))
    responses.post(URL, body=_body("rate_limited"), status=429, content_type="application/json")
    responses.post(URL, body=_body("empty"), content_type="application/json")
    assert list(_client().run_report(PROPERTY, WINDOW.start, WINDOW.end, ["date"], METRICS)) == []
    assert len(responses.calls) == 4


@pytest.mark.parametrize("name", sorted(GRAINS))
def test_rows_carry_source_report_property_and_date(name: str) -> None:
    grain = GRAINS[name]
    for api_row in fixture_rows(name):
        row = shape_row(grain, PROPERTY, api_row)
        assert row["source"] == "ga4" and row["report"] == name and row["property_id"] == PROPERTY
        assert isinstance(row["date"], date) and row["date"].year == 2026
        assert isinstance(row["sessions"], int) and isinstance(row["total_revenue"], float)
        assert set(row) == set(column_hints(grain))


def test_the_other_row_is_kept_and_flagged_and_breakdowns_sum_to_core() -> None:
    core = {
        shape_row(GRAINS["core"], PROPERTY, r)["date"]: shape_row(GRAINS["core"], PROPERTY, r)
        for r in fixture_rows("core")
    }
    rows = [shape_row(GRAINS["channels"], PROPERTY, r) for r in fixture_rows("channels")]
    other = [r for r in rows if r["channel_group"] == OTHER]
    assert len(other) == 3 and all(r["is_other"] for r in other)
    assert not any(r["is_other"] for r in rows if r["channel_group"] != OTHER)
    for day, total in core.items():
        assert sum(r["sessions"] for r in rows if r["date"] == day) == total["sessions"]
        assert sum(r["sessions"] for r in rows if r["date"] == day and not r["is_other"]) < total["sessions"]


def test_bad_shape_is_rejected() -> None:
    with pytest.raises(ValueError):
        shape_row(
            GRAINS["core"],
            PROPERTY,
            {"dimensionValues": [{"value": "20261001"}], "metricValues": [{"value": "1"}]},
        )


def test_source_hints_and_window(fake_client: FakeGA4Client) -> None:
    source = ga4(property_ids=["properties/123456789"], client=fake_client, window=WINDOW)
    assert set(source.resources) == set(GRAINS) | {"sync_windows"}
    hints: dict[str, Any] = dict(source.resources["campaigns"].compute_table_schema())
    assert hints["x-merge-strategy"] == "delete-insert"
    assert [c for c, h in hints["columns"].items() if h.get("merge_key")] == ["property_id", "date"]
    assert hints["x-clickhouse-sort"] == ["property_id", "date", "campaign", "source_name", "medium"]
    assert all(h["nullable"] is False for h in hints["columns"].values())
    rows = list(source.resources["channels"])
    assert len(rows) == 15 and fake_client.calls[0]["property_id"] == PROPERTY
    with pytest.raises(ValueError, match="unknown grains"):
        ga4(property_ids=[PROPERTY], client=fake_client, grains=["devices"])
    with pytest.raises(ValueError, match="credentials_path"):
        ga4(property_ids=[PROPERTY], credentials_path=None)


def _pipeline(tmp_path: Path) -> dlt.Pipeline:
    return dlt.pipeline(
        pipeline_name="ga4_unit",
        destination=dlt.destinations.dummy(completed_prob=1.0),
        dataset_name="ga4",
        pipelines_dir=str(tmp_path),
    )


def test_daily_run_loads_the_window_and_fails_when_empty(tmp_path: Path, fake_client: FakeGA4Client) -> None:
    counts = run_daily(
        _pipeline(tmp_path),
        today=date(2026, 10, 5),
        property_ids=[PROPERTY],
        client=fake_client,
        marts_sql=None,
    )
    assert counts["core"] == 3 and counts["channels"] == 15 and counts["sync_windows"] == 1
    assert fake_client.calls[0]["start"] == date(2026, 9, 28) and fake_client.calls[0]["end"] == date(
        2026, 10, 4
    )
    with pytest.raises(EmptyWindowError):
        run_daily(
            _pipeline(tmp_path / "x"),
            today=date(2027, 1, 1),
            property_ids=[PROPERTY],
            client=fake_client,
            marts_sql=None,
        )
