"""Derived campaign keys: the slug of the name, manual rows protected, unloaded sources skipped."""

from __future__ import annotations

from datetime import datetime

import pytest

from omw.metrics.campaign_keys import (
    CAMPAIGN_KEYS,
    COLUMNS,
    list_campaign_keys,
    set_campaign_key,
    slug,
    sync_campaign_keys,
)
from tests.unit.checks.conftest import FakeWarehouse

NOW = datetime(2026, 10, 5, 4, 0)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Widgets - Prospecting", "widgets_prospecting"),
        ("  Brand / Search (EU) ", "brand_search_eu"),
        ("PMax - Catalog", "pmax_catalog"),
        ("Q4_2026", "q4_2026"),
        ("---", ""),
        ("", ""),
    ],
)
def test_slug(name: str, expected: str) -> None:
    assert slug(name) == expected


def test_sync_writes_derived_rows_and_skips_manual_and_unloaded() -> None:
    warehouse = FakeWarehouse()
    warehouse.objects = {"raw.meta_ads___campaigns", "raw.google_ads___campaigns"}
    warehouse.when("FROM marts.campaign_keys FINAL WHERE origin = 'manual'", [["meta_ads", "2"]])
    warehouse.when(
        "FROM raw.meta_ads___campaigns",
        [["1", "Widgets - Prospecting"], ["2", "Widgets - Retargeting"], ["3", "--"]],
    )
    warehouse.when("FROM raw.google_ads___campaigns", [["20010001", "Brand - Search"]])
    written = sync_campaign_keys(warehouse, now=NOW)
    assert written == {"meta_ads": 1, "google_ads": 1}  # linkedin_ads is not loaded: not even a key
    assert any("CREATE TABLE IF NOT EXISTS marts.campaign_keys" in c for c in warehouse.commands)
    assert any("ADD COLUMN IF NOT EXISTS origin" in c for c in warehouse.commands)
    assert warehouse.inserted == [
        (
            CAMPAIGN_KEYS,
            [["meta_ads", "1", "widgets_prospecting", "Widgets - Prospecting", "derived", NOW]],
            COLUMNS,
        ),
        (
            CAMPAIGN_KEYS,
            [["google_ads", "20010001", "brand_search", "Brand - Search", "derived", NOW]],
            COLUMNS,
        ),
    ]


def test_sync_can_be_limited_to_one_source_without_migrating() -> None:
    warehouse = FakeWarehouse()
    warehouse.objects = {"raw.meta_ads___campaigns", "raw.linkedin_ads___campaigns"}
    warehouse.when("origin = 'manual'", [])
    warehouse.when("FROM raw.linkedin_ads___campaigns", [["800001", "Widgets - Sponsored Content"]])
    written = sync_campaign_keys(warehouse, sources={"linkedin_ads"}, now=NOW, ensure=False)
    assert written == {"linkedin_ads": 1} and warehouse.commands == []
    assert warehouse.inserted[0][1][0][:3] == ["linkedin_ads", "800001", "widgets_sponsored_content"]


def test_set_and_list() -> None:
    warehouse = FakeWarehouse()
    set_campaign_key(warehouse, "google_ads", "20010002", "widgets_prospecting", "Generic - Widgets")
    table, rows, columns = warehouse.inserted[0]
    assert table == CAMPAIGN_KEYS and columns == COLUMNS
    assert rows[0][:5] == ["google_ads", "20010002", "widgets_prospecting", "Generic - Widgets", "manual"]
    with pytest.raises(ValueError, match="must not be empty"):
        set_campaign_key(warehouse, "google_ads", "1", "  ")
    warehouse.when(
        "FROM marts.campaign_keys FINAL WHERE source = 'google_ads'",
        [["google_ads", "1", "k", "n", "manual"]],
    )
    assert list_campaign_keys(warehouse, "google_ads") == [["google_ads", "1", "k", "n", "manual"]]
