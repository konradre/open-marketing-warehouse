"""Google Ads as ``searchStream`` answers: campaign entities and the four daily report grains.

Ad groups add up to their campaign. Search terms cover only part of it (Google hides low-volume terms) and
exist for Search campaigns only; the Performance Max campaign has asset groups instead of ad groups."""

from __future__ import annotations

from datetime import date
from typing import Any

from omw.demo.fixtures.common import Payloads, parts
from omw.demo.funnel import Funnel
from omw.demo.world import AdCampaign, Day, of_source, reported
from omw.eval.generate.rng import Rng

ORDER_VALUE = 139.0
TERMS: dict[str, tuple[str, ...]] = {
    "brand_search": ("example widgets", "example widgets login", "example widgets pricing", "widgets app"),
    "widgets_prospecting": ("widget software", "best widget tool", "widget management", "widgets for teams"),
    "emea_generic": ("widget software europe", "widget tool gdpr", "widget software deutsch", "outil widget"),
}
VISIBLE_SHARE = 0.8
"""The part of a Search ad group's traffic Google names a search term for."""
STREAM_CHUNK = 500
"""Rows per chunk of a stream answer, so the client sees a multi-chunk body."""


def _groups(campaign: AdCampaign) -> list[tuple[str, str, float]]:
    """(id, name, share of the campaign) of its ad groups, or asset groups for Performance Max."""
    names = {
        "SEARCH": (("Exact", 0.62), ("Phrase", 0.38)),
        "DISPLAY": (("All visitors", 0.7), ("Cart abandoners", 0.3)),
        "PERFORMANCE_MAX": (("All products", 0.66), ("Gadgets", 0.34)),
    }[campaign.channel]
    return [(f"{campaign.campaign_id}{n}", name, share) for n, (name, share) in enumerate(names, start=1)]


def _customer(campaign: AdCampaign) -> dict[str, Any]:
    return {
        "resourceName": f"customers/{campaign.account_id}",
        "id": campaign.account_id,
        "currencyCode": campaign.currency,
    }


def _campaign(campaign: AdCampaign) -> dict[str, Any]:
    return {
        "resourceName": f"customers/{campaign.account_id}/campaigns/{campaign.campaign_id}",
        "id": campaign.campaign_id,
        "name": campaign.name,
        "status": "ENABLED",
        "advertisingChannelType": campaign.channel,
    }


def _metrics(part: Day) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "impressions": str(part.impressions),
        "clicks": str(part.clicks),
        "costMicros": str(part.cents * 10_000),
    }
    if part.conversions:  # the REST API leaves zero-valued fields out
        metrics["conversions"] = float(part.conversions)
        metrics["conversionsValue"] = round(part.conversions * ORDER_VALUE, 2)
    return metrics


def _row(campaign: AdCampaign, day: date, part: Day, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "customer": _customer(campaign),
        "campaign": _campaign(campaign),
        **extra,
        "segments": {"date": day.isoformat()},
        "metrics": _metrics(part),
    }


def _stream(rows: list[dict[str, Any]], field_mask: str) -> list[dict[str, Any]]:
    chunks = [rows[start : start + STREAM_CHUNK] for start in range(0, len(rows), STREAM_CHUNK)] or [[]]
    return [
        {"results": chunk, "fieldMask": field_mask, "requestId": f"demo-{n:04d}"}
        for n, chunk in enumerate(chunks, start=1)
    ]


def _entity(campaign: AdCampaign, start: date) -> dict[str, Any]:
    budget = int(campaign.impressions * campaign.ctr * campaign.cpc * 1.2)
    strategy = {"SEARCH": "MAXIMIZE_CONVERSIONS", "DISPLAY": "TARGET_CPA"}.get(
        campaign.channel, "MAXIMIZE_CONVERSION_VALUE"
    )
    return {
        "customer": {**_customer(campaign), "timeZone": "Etc/UTC"},
        "campaign": {
            **_campaign(campaign),
            "servingStatus": "SERVING",
            "biddingStrategyType": strategy,
            "startDate": start.isoformat(),
            "endDate": "2037-12-30",
        },
        "campaignBudget": {"amountMicros": str(budget * 1_000_000)},
    }


def build(rng: Rng, funnel: Funnel, as_of: date) -> Payloads:
    calendar = funnel.calendar
    rows: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("campaigns", "ad_groups", "search_terms", "asset_groups")
    }
    entities = []
    for campaign in of_source("google_ads"):
        entities.append(_entity(campaign, calendar.day_at(campaign.launch)))
        groups = _groups(campaign)
        for day in calendar.span():
            total = reported(rng, calendar, campaign, day, as_of)
            if total is None:
                continue
            key = (campaign.campaign_id, day)
            rows["campaigns"].append(_row(campaign, day, total, {}))
            shares = parts(rng, total, [w for *_, w in groups], *key, "groups")
            for (group_id, name, _), part in zip(groups, shares, strict=True):
                if campaign.channel == "PERFORMANCE_MAX":
                    group = {"id": group_id, "name": name, "status": "ENABLED"}
                    rows["asset_groups"].append(_row(campaign, day, part, {"assetGroup": group}))
                    continue
                group = {"id": group_id, "name": name, "status": "ENABLED"}
                rows["ad_groups"].append(_row(campaign, day, part, {"adGroup": group}))
                terms = TERMS.get(campaign.key) if campaign.channel == "SEARCH" else None
                if not terms:
                    continue
                named = Day(*(int(value * VISIBLE_SHARE) for value in vars(part).values()))
                weights = [1 / (rank + 1) for rank in range(len(terms))]
                for term, piece in zip(terms, parts(rng, named, weights, *key, group_id), strict=True):
                    if piece.impressions:
                        view = {"searchTerm": term, "status": "ADDED" if term == terms[0] else "NONE"}
                        extra = {"adGroup": {"id": group_id, "name": name}, "searchTermView": view}
                        rows["search_terms"].append(_row(campaign, day, piece, extra))
    for name in rows:
        rows[name].sort(key=lambda row: (row["segments"]["date"], row["customer"]["id"]))
    return {
        "campaign_entities": _stream(entities, "customer.id,campaign.id,campaign.name"),
        **{f"{name}_stream": _stream(found, "segments.date,metrics.clicks") for name, found in rows.items()},
    }
