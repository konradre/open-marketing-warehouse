"""Field lists and column hints for the Meta Ads source."""

from __future__ import annotations

from typing import Literal

from dlt.common.schema.typing import TColumnSchema

from omw.sources.search_console.grains import column

InsightsLevel = Literal["campaign", "ad"]

ENTITY_COMMON_FIELDS = (
    "id",
    "account_id",
    "name",
    "status",
    "effective_status",
    "created_time",
    "updated_time",
)

CAMPAIGN_FIELDS = (
    *ENTITY_COMMON_FIELDS,
    "objective",
    "start_time",
    "stop_time",
    "daily_budget",
    "lifetime_budget",
    "buying_type",
)
ADSET_FIELDS = (
    *ENTITY_COMMON_FIELDS,
    "campaign_id",
    "start_time",
    "end_time",
    "daily_budget",
    "lifetime_budget",
    "optimization_goal",
    "billing_event",
    "bid_strategy",
    "bid_amount",
)
AD_FIELDS = (*ENTITY_COMMON_FIELDS, "adset_id", "campaign_id", "creative")

ENTITY_RESOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    # resource name -> (AdAccount method, fields)
    "campaigns": ("get_campaigns", CAMPAIGN_FIELDS),
    "ad_sets": ("get_ad_sets", ADSET_FIELDS),
    "ads": ("get_ads", AD_FIELDS),
}

INSIGHT_FIELDS = (
    "account_id",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "ad_id",
    "ad_name",
    "date_start",
    "date_stop",
    "impressions",
    "clicks",
    "reach",
    "frequency",
    "spend",
    "ctr",
    "cpc",
    "cpm",
    "actions",
    "action_values",
)

ACTION_ATTRIBUTION_WINDOWS = ("7d_click", "1d_view")
"""Meta's default attribution setting; SEMANTICS.md (T6) documents it next to the metrics."""

LEVEL_REPORT: dict[InsightsLevel, str] = {"campaign": "campaigns", "ad": "ads"}
LEVEL_KEYS: dict[InsightsLevel, tuple[str, ...]] = {
    "campaign": ("campaign_id",),
    "ad": ("campaign_id", "adset_id", "ad_id"),
}

INSIGHTS_MERGE_KEY = ("account_id", "date")
"""The slice a run replaces: every (account, day) it fetched, per level table."""


def insights_columns(level: InsightsLevel) -> dict[str, TColumnSchema]:
    hints: dict[str, TColumnSchema] = {
        "source": column("text"),
        "report": column("text"),
        "level": column("text"),
        "account_id": column("text"),
        "currency": column("text"),
        "date": column("date"),
        "campaign_id": column("text"),
        "campaign_name": column("text"),
    }
    if level == "ad":
        for name in ("adset_id", "adset_name", "ad_id", "ad_name"):
            hints[name] = column("text")
    hints.update(
        {
            "impressions": column("bigint"),
            "clicks": column("bigint"),
            "reach": column("bigint"),
            "spend": column("decimal"),
            "frequency": column("double"),
            "ctr": column("double"),
            "cpc": column("double"),
            "cpm": column("double"),
            "actions": column("text"),
            "action_values": column("text"),
        }
    )
    return hints


def insights_sort_key(level: InsightsLevel) -> list[str]:
    return [*INSIGHTS_MERGE_KEY, *LEVEL_KEYS[level]]
