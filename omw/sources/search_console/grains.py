"""Report grains (DESIGN.md §4): stored side by side, never summed together."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from dlt.common.schema.typing import TColumnSchema

from omw.sources.columns import column

__all__ = ["GRAINS", "MERGE_KEY", "METRICS", "SOURCE", "Grain", "column", "column_hints", "shape_row"]

SOURCE = "search_console"

MERGE_KEY: tuple[str, ...] = ("site_url", "search_type", "date")
"""The slice a run replaces: every (property, search type, day) it fetched."""


@dataclass(frozen=True)
class Grain:
    name: str
    dimensions: tuple[str, ...]
    """API dimensions after ``date``, which every grain has. Column names match the API names."""

    @property
    def api_dimensions(self) -> list[str]:
        return ["date", *self.dimensions]

    @property
    def sort_key(self) -> tuple[str, ...]:
        return (*MERGE_KEY, *self.dimensions)


GRAINS: dict[str, Grain] = {
    "core": Grain("core", ()),
    "queries": Grain("queries", ("query",)),
    "pages": Grain("pages", ("page",)),
    "query_page": Grain("query_page", ("query", "page")),
    "countries": Grain("countries", ("country",)),
    "devices": Grain("devices", ("device",)),
}

METRICS: tuple[str, ...] = ("clicks", "impressions", "ctr", "position")


def shape_row(grain: Grain, site_url: str, search_type: str, api_row: dict[str, Any]) -> dict[str, Any]:
    """One warehouse row from one API row (``keys`` in dimension order, then the four metrics)."""
    keys = api_row["keys"]
    if len(keys) != len(grain.api_dimensions):
        raise ValueError(f"{grain.name}: expected {len(grain.api_dimensions)} keys, got {keys!r}")
    row: dict[str, Any] = {
        "source": SOURCE,
        "report": grain.name,
        "site_url": site_url,
        "search_type": search_type,
        "date": date.fromisoformat(keys[0]),
    }
    row.update(zip(grain.dimensions, keys[1:], strict=True))
    row["clicks"] = int(api_row["clicks"])
    row["impressions"] = int(api_row["impressions"])
    row["ctr"] = float(api_row["ctr"])
    row["position"] = float(api_row["position"])
    return row


def column_hints(grain: Grain) -> dict[str, TColumnSchema]:
    hints: dict[str, TColumnSchema] = {
        "source": column("text"),
        "report": column("text"),
        "site_url": column("text"),
        "search_type": column("text"),
        "date": column("date"),
    }
    for dimension in grain.dimensions:
        hints[dimension] = column("text")
    hints["clicks"] = column("bigint")
    hints["impressions"] = column("bigint")
    hints["ctr"] = column("double")
    hints["position"] = column("double")
    return hints
