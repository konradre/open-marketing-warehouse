from __future__ import annotations

from datetime import date

import pytest

from omw.sources.search_console.grains import GRAINS, MERGE_KEY, column_hints, shape_row
from tests.unit.search_console.conftest import SITE, fixture_rows


def test_every_grain_starts_with_date_and_sorts_by_the_slice() -> None:
    assert set(GRAINS) == {"core", "queries", "pages", "query_page", "countries", "devices"}
    for grain in GRAINS.values():
        assert grain.api_dimensions[0] == "date"
        assert grain.sort_key[: len(MERGE_KEY)] == MERGE_KEY
        assert grain.sort_key[len(MERGE_KEY) :] == grain.dimensions


@pytest.mark.parametrize("name", sorted(GRAINS))
def test_rows_carry_source_report_site_and_date(name: str) -> None:
    grain = GRAINS[name]
    for api_row in fixture_rows(name):
        row = shape_row(grain, SITE, "web", api_row)
        assert row["source"] == "search_console"
        assert row["report"] == name
        assert row["site_url"] == SITE
        assert row["search_type"] == "web"
        assert isinstance(row["date"], date)
        for dimension, value in zip(grain.dimensions, api_row["keys"][1:], strict=True):
            assert row[dimension] == value
        assert isinstance(row["clicks"], int) and isinstance(row["impressions"], int)
        assert set(row) == set(column_hints(grain))


def test_breakdowns_never_overcount_core() -> None:
    core = {row["keys"][0]: row["clicks"] for row in fixture_rows("core")}
    for name in ("queries", "pages", "query_page", "countries", "devices"):
        by_day: dict[str, int] = {}
        for row in fixture_rows(name):
            by_day[row["keys"][0]] = by_day.get(row["keys"][0], 0) + row["clicks"]
        for day, clicks in by_day.items():
            assert clicks <= core[day], (name, day)


def test_wrong_key_count_is_rejected() -> None:
    with pytest.raises(ValueError):
        shape_row(
            GRAINS["queries"],
            SITE,
            "web",
            {"keys": ["2026-10-01"], "clicks": 1, "impressions": 1, "ctr": 1, "position": 1},
        )


def test_column_hints_are_never_nullable() -> None:
    for grain in GRAINS.values():
        assert all(hint["nullable"] is False for hint in column_hints(grain).values())
