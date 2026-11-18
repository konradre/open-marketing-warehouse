"""Google Ads: campaign, ad group, search term and Performance Max asset group performance, and the campaign
list. Search terms undercount their campaign: Google withholds the long tail."""

from __future__ import annotations

from decimal import Decimal

from omw.eval.generate.campaigns import Day, delivery, money, of_source, parts
from omw.eval.generate.rng import Rng
from omw.eval.world import Row, World, days

SEARCH_TERMS: dict[str, tuple[str, ...]] = {
    "20010001": ("example widgets", "example.com", "example widgets login", "example pricing", "example app"),
    "20010002": (
        "buy widgets online",
        "best widgets",
        "widget supplier",
        "industrial widgets",
        "widgets wholesale",
        "cheap widgets",
        "widget comparison",
    ),
    "20020001": ("widgets kaufen", "widgets uk", "widget lieferant", "widgets europe", "acheter widgets"),
    "20020002": ("example widgets deutschland", "example widgets uk", "example eu"),
}


def _google_measures(part: Day, value_per_conversion: float) -> Row:
    conversions = part.conversions / 10
    return {
        "impressions": part.impressions,
        "clicks": part.clicks,
        "cost": money(part.cents),
        "conversions": conversions,
        "conversions_value": round(conversions * value_per_conversion, 2),
    }


def _google_raw(row: Row) -> Row:
    raw = {k: v for k, v in row.items() if k != "cost"}
    raw["cost_micros"] = int(row["cost"] * 1_000_000)
    return raw


def build_google(rng: Rng, world: World) -> None:
    for campaign in of_source("google_ads"):
        base = {
            "source": "google_ads",
            "customer_id": campaign.account_id,
            "currency": campaign.currency,
            "campaign_id": campaign.campaign_id,
            "campaign_name": campaign.name,
        }
        value = 80.0 if campaign.currency == "USD" else 72.0
        pmax = campaign.channel == "PERFORMANCE_MAX"
        tail = campaign.campaign_id[-4:]
        groups = [(f"31{tail}1", "Exact"), (f"31{tail}2", "Phrase"), (f"31{tail}3", "Broad")]
        assets = [(f"61{tail}1", "Lifestyle imagery", "ENABLED"), (f"61{tail}2", "Product shots", "ENABLED")]
        terms = SEARCH_TERMS.get(campaign.campaign_id, ())
        for day in days():
            total = delivery(rng, campaign, day)
            if total is None:
                continue
            key = (campaign.campaign_id, day)
            row = {**base, "report": "campaigns", "date": day, "campaign_status": "ENABLED"}
            row |= {"channel_type": campaign.channel, **_google_measures(total, value)}
            world.add("google_ads_campaigns", [row])
            world.add_raw("google_ads___performance_campaigns", [_google_raw(row)])
            if pmax:
                for (asset_id, asset_name, status), part in zip(
                    assets, parts(rng, total, [0.62, 0.38], *key, "assets"), strict=True
                ):
                    row = {**base, "report": "asset_groups", "date": day, "asset_group_id": asset_id}
                    row |= {"asset_group_name": asset_name, "asset_group_status": status}
                    row |= _google_measures(part, value)
                    world.add("google_ads_asset_groups", [row])
                    world.add_raw("google_ads___performance_asset_groups", [_google_raw(row)])
                continue
            group_days = parts(rng, total, [0.5, 0.3, 0.2], *key, "ad_groups")
            for (group_id, group_name), part in zip(groups, group_days, strict=True):
                row = {**base, "report": "ad_groups", "date": day, "ad_group_id": group_id}
                row |= {"ad_group_name": group_name, "ad_group_status": "ENABLED"}
                row |= _google_measures(part, value)
                world.add("google_ads_ad_groups", [row])
                world.add_raw("google_ads___performance_ad_groups", [_google_raw(row)])
            if not terms:
                continue
            # Search terms are reported for the first ad group only and Google withholds the long tail:
            # about a fifth of that ad group's volume never shows up as a term.
            group_id, group_name = groups[0]
            shown = Day(
                *(
                    int(v * 0.8)
                    for v in (
                        group_days[0].impressions,
                        group_days[0].clicks,
                        group_days[0].cents,
                        group_days[0].conversions,
                    )
                )
            )
            weights = [1 / (rank + 1.5) for rank in range(len(terms))]
            for rank, (term, part) in enumerate(
                zip(terms, parts(rng, shown, weights, *key, "terms"), strict=True)
            ):
                row = {**base, "report": "search_terms", "date": day, "ad_group_id": group_id}
                row |= {"ad_group_name": group_name, "search_term": term}
                row |= {"search_term_status": "ADDED" if rank < 2 else "NONE"}
                row |= _google_measures(part, value)
                world.add("google_ads_search_terms", [row])
                world.add_raw("google_ads___performance_search_terms", [_google_raw(row)])
        budget = int(campaign.impressions * campaign.ctr * campaign.cpc * 1.2)
        world.add_raw(
            "google_ads___campaigns",
            [
                {
                    "source": "google_ads",
                    "customer_id": campaign.account_id,
                    "currency": campaign.currency,
                    "time_zone": "America/New_York" if campaign.currency == "USD" else "Europe/Berlin",
                    "campaign_id": campaign.campaign_id,
                    "campaign_name": campaign.name,
                    "campaign_status": "ENABLED",
                    "serving_status": "SERVING",
                    "channel_type": campaign.channel,
                    "bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
                    "start_date": campaign.start.isoformat(),
                    "end_date": "2037-12-30",
                    "budget_micros": budget * 1_000_000,
                }
            ],
        )
        world.add(
            "google_ads_campaign_list",
            [
                {
                    "source": "google_ads",
                    "customer_id": campaign.account_id,
                    "currency": campaign.currency,
                    "campaign_id": campaign.campaign_id,
                    "campaign_name": campaign.name,
                    "channel_type": campaign.channel,
                    "daily_budget": Decimal(budget),
                }
            ],
        )
