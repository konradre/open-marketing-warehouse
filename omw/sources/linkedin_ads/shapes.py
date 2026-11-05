"""Flatten LinkedIn's nested objects (URNs, money, audit stamps, date ranges) into warehouse rows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from dlt.common.schema.typing import TColumnSchema

from omw.sources.columns import column

SOURCE = "linkedin_ads"


def urn_id(value: Any) -> str:
    """``urn:li:sponsoredCampaign:123`` -> ``123``; a plain id stays."""
    return str(value).rsplit(":", 1)[-1] if value else ""


def money(value: Any) -> tuple[Decimal, str]:
    if isinstance(value, Mapping):
        amount = value.get("amount")
        return Decimal(str(amount)) if amount not in (None, "") else Decimal(0), str(
            value.get("currencyCode") or ""
        )
    return Decimal(0), ""


def stamp(value: Any) -> datetime:
    """LinkedIn audit stamps are epoch milliseconds."""
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC) if value else datetime(1970, 1, 1, tzinfo=UTC)


def range_date(value: Any) -> date:
    if isinstance(value, Mapping) and value.get("year"):
        return date(int(value["year"]), int(value["month"]), int(value["day"]))
    return date(1970, 1, 1)


def shape_account(obj: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "name": str(obj.get("name") or ""),
        "status": str(obj.get("status") or ""),
        "type": str(obj.get("type") or ""),
        "currency": str(obj.get("currency") or ""),
        "test": bool(obj.get("test", False)),
    }


ACCOUNT_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "name": column("text"),
    "status": column("text"),
    "type": column("text"),
    "currency": column("text"),
    "test": column("bool"),
}


def shape_campaign_group(obj: Mapping[str, Any]) -> dict[str, Any]:
    total, total_ccy = money(obj.get("totalBudget"))
    daily, daily_ccy = money(obj.get("dailyBudget"))
    schedule = obj.get("runSchedule") or {}
    audit = obj.get("changeAuditStamps") or {}
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "account_id": urn_id(obj.get("account")),
        "name": str(obj.get("name") or ""),
        "status": str(obj.get("status") or ""),
        "total_budget": total,
        "daily_budget": daily,
        "currency": daily_ccy or total_ccy,
        "budget_optimization": bool(obj.get("budgetOptimizationEnabled", False)),
        "objective_type": str(obj.get("objectiveType") or ""),
        "run_start": stamp(schedule.get("start")),
        "run_end": stamp(schedule.get("end")),
        "created_at": stamp((audit.get("created") or {}).get("time")),
        "modified_at": stamp((audit.get("lastModified") or {}).get("time")),
    }


CAMPAIGN_GROUP_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "account_id": column("text"),
    "name": column("text"),
    "status": column("text"),
    "total_budget": column("decimal"),
    "daily_budget": column("decimal"),
    "currency": column("text"),
    "budget_optimization": column("bool"),
    "objective_type": column("text"),
    "run_start": column("timestamp"),
    "run_end": column("timestamp"),
    "created_at": column("timestamp"),
    "modified_at": column("timestamp"),
}


def shape_campaign(obj: Mapping[str, Any]) -> dict[str, Any]:
    daily, daily_ccy = money(obj.get("dailyBudget"))
    unit, unit_ccy = money(obj.get("unitCost"))
    schedule = obj.get("runSchedule") or {}
    audit = obj.get("changeAuditStamps") or {}
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "account_id": urn_id(obj.get("account")),
        "campaign_group_id": urn_id(obj.get("campaignGroup")),
        "name": str(obj.get("name") or ""),
        "status": str(obj.get("status") or ""),
        "type": str(obj.get("type") or ""),
        "objective_type": str(obj.get("objectiveType") or ""),
        "cost_type": str(obj.get("costType") or ""),
        "optimization_target_type": str(obj.get("optimizationTargetType") or ""),
        "daily_budget": daily,
        "unit_cost": unit,
        "currency": daily_ccy or unit_ccy,
        "run_start": stamp(schedule.get("start")),
        "run_end": stamp(schedule.get("end")),
        "created_at": stamp((audit.get("created") or {}).get("time")),
        "modified_at": stamp((audit.get("lastModified") or {}).get("time")),
    }


CAMPAIGN_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "account_id": column("text"),
    "campaign_group_id": column("text"),
    "name": column("text"),
    "status": column("text"),
    "type": column("text"),
    "objective_type": column("text"),
    "cost_type": column("text"),
    "optimization_target_type": column("text"),
    "daily_budget": column("decimal"),
    "unit_cost": column("decimal"),
    "currency": column("text"),
    "run_start": column("timestamp"),
    "run_end": column("timestamp"),
    "created_at": column("timestamp"),
    "modified_at": column("timestamp"),
}


def shape_creative(obj: Mapping[str, Any]) -> dict[str, Any]:
    audit = obj.get("changeAuditStamps") or {}
    return {
        "source": SOURCE,
        "id": urn_id(obj.get("id")),
        "account_id": urn_id(obj.get("account")),
        "campaign_id": urn_id(obj.get("campaign")),
        "intended_status": str(obj.get("intendedStatus") or ""),
        "is_serving": bool(obj.get("isServing", False)),
        "content_reference": str(obj.get("content", {}).get("reference") or "")
        if isinstance(obj.get("content"), Mapping)
        else "",
        "created_at": stamp((audit.get("created") or {}).get("time") or obj.get("createdAt")),
        "modified_at": stamp((audit.get("lastModified") or {}).get("time") or obj.get("lastModifiedAt")),
    }


CREATIVE_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "account_id": column("text"),
    "campaign_id": column("text"),
    "intended_status": column("text"),
    "is_serving": column("bool"),
    "content_reference": column("text"),
    "created_at": column("timestamp"),
    "modified_at": column("timestamp"),
}

ANALYTICS_FIELDS = (
    "dateRange",
    "pivotValues",
    "impressions",
    "clicks",
    "costInLocalCurrency",
    "externalWebsiteConversions",
    "landingPageClicks",
    "likes",
    "shares",
    "follows",
)


def shape_analytics(account_id: str, currency: str, obj: Mapping[str, Any]) -> dict[str, Any]:
    pivots = obj.get("pivotValues") or []
    cost = obj.get("costInLocalCurrency")
    return {
        "source": SOURCE,
        "report": "campaigns",
        "account_id": account_id,
        "currency": currency,
        "date": range_date((obj.get("dateRange") or {}).get("start")),
        "campaign_id": urn_id(pivots[0]) if pivots else "",
        "impressions": int(obj.get("impressions") or 0),
        "clicks": int(obj.get("clicks") or 0),
        "cost": Decimal(str(cost)) if cost not in (None, "") else Decimal(0),
        "conversions": int(obj.get("externalWebsiteConversions") or 0),
        "landing_page_clicks": int(obj.get("landingPageClicks") or 0),
        "likes": int(obj.get("likes") or 0),
        "shares": int(obj.get("shares") or 0),
        "follows": int(obj.get("follows") or 0),
    }


ANALYTICS_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "report": column("text"),
    "account_id": column("text"),
    "currency": column("text"),
    "date": column("date"),
    "campaign_id": column("text"),
    "impressions": column("bigint"),
    "clicks": column("bigint"),
    "cost": column("decimal"),
    "conversions": column("bigint"),
    "landing_page_clicks": column("bigint"),
    "likes": column("bigint"),
    "shares": column("bigint"),
    "follows": column("bigint"),
}
