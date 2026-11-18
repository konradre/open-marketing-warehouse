"""GAQL for the report grains (DESIGN.md §4): ``campaigns``, ``ad_groups``, ``search_terms`` and
``asset_groups`` (Performance Max) daily, and campaign entities."""

from __future__ import annotations

from dataclasses import dataclass

from omw.sources.windows import Window

METRIC_FIELDS = (
    "metrics.impressions",
    "metrics.clicks",
    "metrics.cost_micros",
    "metrics.conversions",
    "metrics.conversions_value",
)


@dataclass(frozen=True)
class Report:
    name: str
    resource: str
    """The GAQL FROM resource."""
    fields: tuple[str, ...]
    """Attribute fields selected before ``segments.date`` and the metrics."""
    keys: tuple[str, ...]
    """Row columns after the merge key, in sort order."""


REPORTS: dict[str, Report] = {
    "campaigns": Report(
        "campaigns",
        "campaign",
        (
            "customer.id",
            "customer.currency_code",
            "campaign.id",
            "campaign.name",
            "campaign.status",
            "campaign.advertising_channel_type",
        ),
        ("campaign_id",),
    ),
    "ad_groups": Report(
        "ad_groups",
        "ad_group",
        (
            "customer.id",
            "customer.currency_code",
            "campaign.id",
            "campaign.name",
            "ad_group.id",
            "ad_group.name",
            "ad_group.status",
        ),
        ("campaign_id", "ad_group_id"),
    ),
    "search_terms": Report(
        "search_terms",
        "search_term_view",
        (
            "customer.id",
            "customer.currency_code",
            "campaign.id",
            "campaign.name",
            "ad_group.id",
            "ad_group.name",
            "search_term_view.search_term",
            "search_term_view.status",
        ),
        ("campaign_id", "ad_group_id", "search_term"),
    ),
    "asset_groups": Report(
        "asset_groups",
        "asset_group",
        (
            "customer.id",
            "customer.currency_code",
            "campaign.id",
            "campaign.name",
            "asset_group.id",
            "asset_group.name",
            "asset_group.status",
        ),
        ("campaign_id", "asset_group_id"),
    ),
}

ENTITY_FIELDS = (
    "customer.id",
    "customer.currency_code",
    "customer.time_zone",
    "campaign.id",
    "campaign.name",
    "campaign.status",
    "campaign.serving_status",
    "campaign.advertising_channel_type",
    "campaign.bidding_strategy_type",
    "campaign.start_date",
    "campaign.end_date",
    "campaign_budget.amount_micros",
)


def performance_query(report: Report, window: Window) -> str:
    fields = ", ".join((*report.fields, "segments.date", *METRIC_FIELDS))
    # No filter on campaign.status: a removed campaign still spent on the days it ran, and every sync
    # replaces the whole (customer, day) slice, so leaving it out would delete its spend from the window.
    return (
        f"SELECT {fields} FROM {report.resource} "
        f"WHERE segments.date BETWEEN '{window.start.isoformat()}' AND '{window.end.isoformat()}' "
        "ORDER BY segments.date"
    )


def entities_query() -> str:
    fields = ", ".join(ENTITY_FIELDS)
    # Removed campaigns included: the entity table is merged on the id and never deletes, so a campaign
    # left out after its removal would keep its last status and budget. The campaign list view hides them.
    return f"SELECT {fields} FROM campaign ORDER BY campaign.id"
