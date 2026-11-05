"""Flatten ``GoogleAdsRow`` results (camelCase JSON) into warehouse rows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from dlt.common.schema.typing import TColumnSchema

from omw.sources.google_ads.queries import Report
from omw.sources.search_console.grains import column

SOURCE = "google_ads"
MERGE_KEY = ("customer_id", "date")


def _get(row: Mapping[str, Any], path: str, default: Any = "") -> Any:
    current: Any = row
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def _int(value: Any) -> int:
    return int(value) if value not in (None, "") else 0


def _float(value: Any) -> float:
    return float(value) if value not in (None, "") else 0.0


def shape_performance(report: Report, item: Mapping[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source": SOURCE,
        "report": report.name,
        "customer_id": str(_get(item, "customer.id")),
        "currency": str(_get(item, "customer.currencyCode")),
        "date": date.fromisoformat(str(_get(item, "segments.date"))),
        "campaign_id": str(_get(item, "campaign.id")),
        "campaign_name": str(_get(item, "campaign.name")),
    }
    if report.name == "campaigns":
        row["campaign_status"] = str(_get(item, "campaign.status"))
        row["channel_type"] = str(_get(item, "campaign.advertisingChannelType"))
    elif report.name == "search_terms":
        row["ad_group_id"] = str(_get(item, "adGroup.id"))
        row["ad_group_name"] = str(_get(item, "adGroup.name"))
        row["search_term"] = str(_get(item, "searchTermView.searchTerm"))
        row["search_term_status"] = str(_get(item, "searchTermView.status"))
    elif report.name == "asset_groups":
        row["asset_group_id"] = str(_get(item, "assetGroup.id"))
        row["asset_group_name"] = str(_get(item, "assetGroup.name"))
        row["asset_group_status"] = str(_get(item, "assetGroup.status"))
    else:
        row["ad_group_id"] = str(_get(item, "adGroup.id"))
        row["ad_group_name"] = str(_get(item, "adGroup.name"))
        row["ad_group_status"] = str(_get(item, "adGroup.status"))
    row.update(
        {
            "impressions": _int(_get(item, "metrics.impressions")),
            "clicks": _int(_get(item, "metrics.clicks")),
            "cost_micros": _int(_get(item, "metrics.costMicros")),
            "conversions": _float(_get(item, "metrics.conversions")),
            "conversions_value": _float(_get(item, "metrics.conversionsValue")),
        }
    )
    return row


def performance_columns(report: Report) -> dict[str, TColumnSchema]:
    hints: dict[str, TColumnSchema] = {
        "source": column("text"),
        "report": column("text"),
        "customer_id": column("text"),
        "currency": column("text"),
        "date": column("date"),
        "campaign_id": column("text"),
        "campaign_name": column("text"),
    }
    if report.name == "campaigns":
        hints["campaign_status"] = column("text")
        hints["channel_type"] = column("text")
    elif report.name == "search_terms":
        hints["ad_group_id"] = column("text")
        hints["ad_group_name"] = column("text")
        hints["search_term"] = column("text")
        hints["search_term_status"] = column("text")
    elif report.name == "asset_groups":
        hints["asset_group_id"] = column("text")
        hints["asset_group_name"] = column("text")
        hints["asset_group_status"] = column("text")
    else:
        hints["ad_group_id"] = column("text")
        hints["ad_group_name"] = column("text")
        hints["ad_group_status"] = column("text")
    hints.update(
        {
            "impressions": column("bigint"),
            "clicks": column("bigint"),
            "cost_micros": column("bigint"),
            "conversions": column("double"),
            "conversions_value": column("double"),
        }
    )
    return hints


def shape_entity(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "customer_id": str(_get(item, "customer.id")),
        "currency": str(_get(item, "customer.currencyCode")),
        "time_zone": str(_get(item, "customer.timeZone")),
        "campaign_id": str(_get(item, "campaign.id")),
        "campaign_name": str(_get(item, "campaign.name")),
        "campaign_status": str(_get(item, "campaign.status")),
        "serving_status": str(_get(item, "campaign.servingStatus")),
        "channel_type": str(_get(item, "campaign.advertisingChannelType")),
        "bidding_strategy_type": str(_get(item, "campaign.biddingStrategyType")),
        "start_date": str(_get(item, "campaign.startDate")),
        "end_date": str(_get(item, "campaign.endDate")),
        "budget_micros": _int(_get(item, "campaignBudget.amountMicros")),
    }


ENTITY_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "customer_id": column("text"),
    "currency": column("text"),
    "time_zone": column("text"),
    "campaign_id": column("text"),
    "campaign_name": column("text"),
    "campaign_status": column("text"),
    "serving_status": column("text"),
    "channel_type": column("text"),
    "bidding_strategy_type": column("text"),
    "start_date": column("text"),
    "end_date": column("text"),
    "budget_micros": column("bigint"),
}
