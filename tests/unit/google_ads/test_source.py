"""The google_ads source and pipeline on the dummy destination."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import dlt
import pytest

from omw.pipelines.google_ads import EmptyWindowError, run_daily
from omw.sources.google_ads import RE_PULL_DAYS, google_ads
from omw.sources.windows import Window, trailing_window
from tests.unit.google_ads.conftest import CUSTOMER, FakeGoogleAdsClient

TODAY = date(2026, 10, 5)
WINDOW = Window(date(2026, 10, 1), date(2026, 10, 3))


def test_resources_and_hints(fake_client: FakeGoogleAdsClient) -> None:
    source = google_ads(customer_ids=[CUSTOMER], client=fake_client, window=WINDOW)
    assert set(source.resources) == {
        "campaigns",
        "performance_campaigns",
        "performance_ad_groups",
        "sync_windows",
    }
    perf: dict[str, Any] = dict(source.resources["performance_ad_groups"].compute_table_schema())
    assert perf["write_disposition"] == "merge" and perf["x-merge-strategy"] == "delete-insert"
    assert [c for c, h in perf["columns"].items() if h.get("merge_key")] == ["customer_id", "date"]
    assert perf["x-clickhouse-sort"] == ["customer_id", "date", "campaign_id", "ad_group_id"]
    assert all(h["nullable"] is False for h in perf["columns"].values())
    entities: dict[str, Any] = dict(source.resources["campaigns"].compute_table_schema())
    assert [c for c, h in entities["columns"].items() if h.get("primary_key")] == ["campaign_id"]
    assert entities["x-clickhouse-sort"] == ["campaign_id"]


def test_rows_cover_the_window_per_customer(fake_client: FakeGoogleAdsClient) -> None:
    source = google_ads(customer_ids=[CUSTOMER, "987-654-3210"], client=fake_client, window=WINDOW)
    rows = list(source.resources["performance_campaigns"])
    assert len(rows) == 18  # 2 customers x 3 days x 3 campaigns
    assert len(list(source.resources["campaigns"])) == 6
    assert {c for c, _ in fake_client.queries} == {CUSTOMER, "9876543210"}
    assert all(
        "BETWEEN '2026-10-01' AND '2026-10-03'" in q for _, q in fake_client.queries if "segments.date" in q
    )


def test_default_window_is_thirty_days_ending_yesterday(fake_client: FakeGoogleAdsClient) -> None:
    source = google_ads(customer_ids=[CUSTOMER], client=fake_client, today=TODAY)
    list(source.resources["performance_campaigns"])
    assert RE_PULL_DAYS == 30
    assert trailing_window(TODAY, 30) == Window(date(2026, 9, 5), date(2026, 10, 4))
    assert "BETWEEN '2026-09-05' AND '2026-10-04'" in fake_client.queries[0][1]


def test_validation(fake_client: FakeGoogleAdsClient) -> None:
    with pytest.raises(ValueError, match="unknown reports"):
        google_ads(customer_ids=[CUSTOMER], client=fake_client, reports=["keywords"])
    with pytest.raises(ValueError, match="developer_token"):
        google_ads(
            customer_ids=[CUSTOMER],
            developer_token=None,
            client_id=None,
            client_secret=None,
            refresh_token=None,
        )


def _pipeline(tmp_path: Path) -> dlt.Pipeline:
    return dlt.pipeline(
        pipeline_name="google_ads_unit",
        destination=dlt.destinations.dummy(completed_prob=1.0),
        dataset_name="google_ads",
        pipelines_dir=str(tmp_path),
    )


def test_daily_run_loads_the_window(tmp_path: Path, fake_client: FakeGoogleAdsClient) -> None:
    counts = run_daily(
        _pipeline(tmp_path), today=TODAY, customer_ids=[CUSTOMER], client=fake_client, marts_sql=None
    )
    assert counts["performance_campaigns"] == 9 and counts["performance_ad_groups"] == 9
    assert counts["campaigns"] == 3 and counts["sync_windows"] == 1


def test_daily_run_fails_on_an_empty_window(tmp_path: Path, fake_client: FakeGoogleAdsClient) -> None:
    with pytest.raises(EmptyWindowError):
        run_daily(
            _pipeline(tmp_path),
            today=date(2027, 6, 1),
            customer_ids=[CUSTOMER],
            client=fake_client,
            marts_sql=None,
        )


def test_a_failed_customer_fails_the_run_and_loads_nothing(
    tmp_path: Path, fake_client: FakeGoogleAdsClient
) -> None:
    fake_client.fail_customers = {CUSTOMER}
    pipeline = _pipeline(tmp_path)
    with pytest.raises(Exception, match="simulated outage"):
        run_daily(pipeline, today=TODAY, customer_ids=[CUSTOMER], client=fake_client, marts_sql=None)
    assert pipeline.last_trace is None or not pipeline.last_trace.last_load_info
