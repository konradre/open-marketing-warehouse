"""Meta Ads: campaign insights, the age and gender, country, placement and ad grains, and the entity lists.
The ads grain leaves out the ad of a paused ad set, so it undercounts its campaign as the real one does."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from omw.eval.generate.campaigns import Campaign, Day, delivery, money, of_source, parts
from omw.eval.generate.rng import Rng
from omw.eval.world import START, Row, World, days

AGES = (("18-24", 0.14), ("25-34", 0.36), ("35-44", 0.31), ("45-54", 0.19))
GENDERS = (("female", 0.47), ("male", 0.53))
COUNTRIES = (("US", 0.58), ("CA", 0.14), ("GB", 0.17), ("DE", 0.11))
PLACEMENTS = (
    (("facebook", "feed"), 0.41),
    (("instagram", "feed"), 0.27),
    (("instagram", "instagram_stories"), 0.19),
    (("facebook", "facebook_reels"), 0.13),
)


def _meta_measures(part: Day) -> Row:
    return {
        "impressions": part.impressions,
        "clicks": part.clicks,
        "reach": int(part.impressions * 0.64),
        "spend": money(part.cents),
    }


def _meta_raw(row: Row, level: str = "campaign") -> Row:
    """The rate columns Meta reports alongside, which the views pass through untouched."""
    impressions, clicks, spend = row["impressions"], row["clicks"], float(row["spend"])
    return {
        **row,
        "level": level,
        "frequency": impressions / row["reach"] if row["reach"] else 0.0,
        "ctr": 100 * clicks / impressions if impressions else 0.0,
        "cpc": spend / clicks if clicks else 0.0,
        "cpm": 1000 * spend / impressions if impressions else 0.0,
        "actions": "[]",
        "action_values": "[]",
    }


def _meta_ads(campaign: Campaign) -> list[tuple[str, str, str, str, bool]]:
    """(ad set id, ad set name, ad id, ad name, ad set is live). The third ad sits in a paused ad set."""
    tail = campaign.campaign_id[-2:]
    return [
        (f"2386000000000{tail}1", "Broad", f"2387000000000{tail}1", "Static image", True),
        (f"2386000000000{tail}1", "Broad", f"2387000000000{tail}2", "Carousel", True),
        (f"2386000000000{tail}2", "Lookalike (paused)", f"2387000000000{tail}3", "Video 15s", False),
    ]


def build_meta(rng: Rng, world: World) -> None:
    for campaign in of_source("meta_ads"):
        base = {
            "source": "meta_ads",
            "account_id": campaign.account_id,
            "currency": campaign.currency,
            "campaign_id": campaign.campaign_id,
            "campaign_name": campaign.name,
        }
        ads = _meta_ads(campaign)
        for day in days():
            total = delivery(rng, campaign, day)
            if total is None:
                continue
            key = (campaign.campaign_id, day)
            row = {**base, "report": "campaigns", "date": day, **_meta_measures(total)}
            world.add("meta_ads_campaigns", [row])
            world.add_raw("meta_ads___insights_campaigns", [_meta_raw(row)])

            segments = [(age, gender, wa * wg) for age, wa in AGES for gender, wg in GENDERS]
            for (age, gender, _), part in zip(
                segments, parts(rng, total, [w for *_, w in segments], *key, "age_gender"), strict=True
            ):
                row = {**base, "report": "age_gender", "date": day, "age": age, "gender": gender}
                row |= _meta_measures(part)
                world.add("meta_ads_age_gender", [row])
                world.add_raw("meta_ads___insights_age_gender", [_meta_raw(row)])

            for (country, _), part in zip(
                COUNTRIES, parts(rng, total, [w for _, w in COUNTRIES], *key, "countries"), strict=True
            ):
                row = {**base, "report": "countries", "date": day, "country": country, **_meta_measures(part)}
                world.add("meta_ads_countries", [row])
                world.add_raw("meta_ads___insights_countries", [_meta_raw(row)])

            for ((platform, position), _), part in zip(
                PLACEMENTS, parts(rng, total, [w for _, w in PLACEMENTS], *key, "placements"), strict=True
            ):
                row = {**base, "report": "placements", "date": day, "publisher_platform": platform}
                row |= {"platform_position": position, **_meta_measures(part)}
                world.add("meta_ads_placements", [row])
                world.add_raw("meta_ads___insights_placements", [_meta_raw(row)])

            for (adset_id, adset_name, ad_id, ad_name, live), part in zip(
                ads, parts(rng, total, [0.5, 0.32, 0.18], *key, "ads"), strict=True
            ):
                if not live:
                    continue
                row = {**base, "report": "ads", "date": day, "adset_id": adset_id, "adset_name": adset_name}
                row |= {"ad_id": ad_id, "ad_name": ad_name, **_meta_measures(part)}
                world.add("meta_ads_ads", [row])
                world.add_raw("meta_ads___insights_ads", [_meta_raw(row, "ad")])

        _meta_entities(world, campaign, ads)


def _meta_entities(world: World, campaign: Campaign, ads: list[tuple[str, str, str, str, bool]]) -> None:
    created = datetime(2026, 5, 20, 9, 0) if campaign.start == START else datetime(2026, 7, 6, 9, 0)
    budget = int(campaign.impressions * campaign.ctr * campaign.cpc * 1.2)
    objective = "OUTCOME_LEADS" if "Lead" in campaign.name else "OUTCOME_SALES"
    world.add_raw(
        "meta_ads___campaigns",
        [
            {
                "id": campaign.campaign_id,
                "account_id": campaign.account_id,
                "name": campaign.name,
                "status": "ACTIVE",
                "effective_status": "ACTIVE",
                "objective": objective,
                "buying_type": "AUCTION",
                "daily_budget": str(budget * 100),
                "lifetime_budget": "",
                "start_time": datetime.combine(campaign.start, datetime.min.time()),
                "stop_time": None,
                "created_time": created,
                "updated_time": created,
            }
        ],
    )
    world.add(
        "meta_ads_campaign_list",
        [
            {
                "source": "meta_ads",
                "account_id": campaign.account_id,
                "currency": campaign.currency,
                "campaign_id": campaign.campaign_id,
                "campaign_name": campaign.name,
                "status": "ACTIVE",
                "objective": objective,
                "daily_budget": Decimal(budget),
            }
        ],
    )
    seen: set[str] = set()
    for adset_id, adset_name, ad_id, ad_name, live in ads:
        status = "ACTIVE" if live else "PAUSED"
        if adset_id not in seen:
            seen.add(adset_id)
            world.add_raw(
                "meta_ads___ad_sets",
                [
                    {
                        "id": adset_id,
                        "account_id": campaign.account_id,
                        "name": adset_name,
                        "status": status,
                        "effective_status": status,
                        "campaign_id": campaign.campaign_id,
                        "optimization_goal": "OFFSITE_CONVERSIONS",
                        "billing_event": "IMPRESSIONS",
                        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                        "bid_amount": "",
                        "daily_budget": "",
                        "lifetime_budget": "",
                        "start_time": None,
                        "end_time": None,
                        "created_time": created,
                        "updated_time": created,
                    }
                ],
            )
        world.add_raw(
            "meta_ads___ads",
            [
                {
                    "id": ad_id,
                    "account_id": campaign.account_id,
                    "name": ad_name,
                    "status": status,
                    "effective_status": status,
                    "adset_id": adset_id,
                    "campaign_id": campaign.campaign_id,
                    "creative": None,
                    "created_time": created,
                    "updated_time": created,
                }
            ],
        )
