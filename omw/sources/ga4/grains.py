"""GA4 report grains (DESIGN.md §4), stored side by side and never summed together."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from dlt.common.schema.typing import TColumnSchema

from omw.sources.columns import column

SOURCE = "ga4"
OTHER = "(other)"
"""GA4's bucket for values beyond its cardinality limits; kept and flagged, never dropped."""

MERGE_KEY: tuple[str, ...] = ("property_id", "date")

METRICS: tuple[str, ...] = (
    "sessions",
    "totalUsers",
    "newUsers",
    "engagedSessions",
    "screenPageViews",
    "keyEvents",
    "totalRevenue",
)
METRIC_COLUMNS: dict[str, str] = {
    "sessions": "sessions",
    "totalUsers": "total_users",
    "newUsers": "new_users",
    "engagedSessions": "engaged_sessions",
    "screenPageViews": "page_views",
    "keyEvents": "key_events",
    "totalRevenue": "total_revenue",
}
INTEGER_METRICS = {"sessions", "total_users", "new_users", "engaged_sessions", "page_views"}


@dataclass(frozen=True)
class Grain:
    name: str
    dimensions: tuple[str, ...]
    """GA4 dimension names after ``date``."""
    columns: tuple[str, ...]
    """Row column names for ``dimensions``, in order."""

    @property
    def api_dimensions(self) -> list[str]:
        return ["date", *self.dimensions]

    @property
    def sort_key(self) -> tuple[str, ...]:
        return (*MERGE_KEY, *self.columns)


GRAINS: dict[str, Grain] = {
    "core": Grain("core", (), ()),
    "channels": Grain("channels", ("sessionDefaultChannelGroup",), ("channel_group",)),
    "landing_pages": Grain("landing_pages", ("landingPage",), ("landing_page",)),
    "campaigns": Grain(
        "campaigns",
        ("sessionCampaignName", "sessionSource", "sessionMedium"),
        ("campaign", "source_name", "medium"),
    ),
}


def _ga4_date(value: str) -> date:
    """GA4 returns ``YYYYMMDD``."""
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def shape_row(grain: Grain, property_id: str, api_row: dict[str, Any]) -> dict[str, Any]:
    dims = [v["value"] for v in api_row["dimensionValues"]]
    metrics = [v["value"] for v in api_row["metricValues"]]
    if len(dims) != len(grain.api_dimensions) or len(metrics) != len(METRICS):
        raise ValueError(f"{grain.name}: unexpected row shape {api_row!r}")
    row: dict[str, Any] = {
        "source": SOURCE,
        "report": grain.name,
        "property_id": property_id,
        "date": _ga4_date(dims[0]),
    }
    row.update(zip(grain.columns, dims[1:], strict=True))
    row["is_other"] = any(v == OTHER for v in dims[1:])
    for name, value in zip(METRICS, metrics, strict=True):
        col = METRIC_COLUMNS[name]
        row[col] = int(float(value)) if col in INTEGER_METRICS else float(value)
    return row


def column_hints(grain: Grain) -> dict[str, TColumnSchema]:
    hints: dict[str, TColumnSchema] = {
        "source": column("text"),
        "report": column("text"),
        "property_id": column("text"),
        "date": column("date"),
    }
    for name in grain.columns:
        hints[name] = column("text")
    hints["is_other"] = column("bool")
    for col in METRIC_COLUMNS.values():
        hints[col] = column("bigint" if col in INTEGER_METRICS else "double")
    return hints
