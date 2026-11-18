"""LinkedIn Ads: campaign and creative analytics, the account, the campaign group and the entity lists."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from omw.eval.generate.campaigns import LINKEDIN_ACCOUNT, Campaign, Day, delivery, money, of_source, parts
from omw.eval.generate.rng import Rng
from omw.eval.world import Row, World, days


def _measures(part: Day) -> Row:
    return {
        "impressions": part.impressions,
        "clicks": part.clicks,
        "cost": money(part.cents),
        "conversions": part.conversions,
        "landing_page_clicks": int(part.clicks * 0.8),
        "likes": int(part.clicks * 0.1),
        "shares": int(part.clicks * 0.03),
        "follows": int(part.clicks * 0.02),
    }


def linkedin_creatives(campaign: Campaign) -> list[tuple[str, str, bool]]:
    """(creative id, intended status, is serving)."""
    tail = campaign.campaign_id[-2:]
    return [(f"9100{tail}1", "ACTIVE", True), (f"9100{tail}2", "ACTIVE", True)]


def build_linkedin(rng: Rng, world: World) -> None:
    world.add_raw(
        "linkedin_ads___ad_accounts",
        [
            {
                "source": "linkedin_ads",
                "id": LINKEDIN_ACCOUNT,
                "name": "Example Widgets",
                "status": "ACTIVE",
                "type": "BUSINESS",
                "currency": "USD",
                "test": False,
            }
        ],
    )
    created = datetime(2026, 5, 18, 10, 0)
    world.add_raw(
        "linkedin_ads___campaign_groups",
        [
            {
                "source": "linkedin_ads",
                "id": "700001",
                "account_id": LINKEDIN_ACCOUNT,
                "name": "FY26 Demand",
                "status": "ACTIVE",
                "total_budget": Decimal(120000),
                "daily_budget": Decimal(900),
                "currency": "USD",
                "budget_optimization": True,
                "objective_type": "LEAD_GENERATION",
                "run_start": created,
                "run_end": datetime(2026, 12, 31),
                "created_at": created,
                "modified_at": created,
            }
        ],
    )
    for campaign in of_source("linkedin_ads"):
        base = {
            "source": "linkedin_ads",
            "account_id": campaign.account_id,
            "currency": campaign.currency,
            "campaign_id": campaign.campaign_id,
        }
        creatives = linkedin_creatives(campaign)
        for day in days():
            total = delivery(rng, campaign, day)
            if total is None:
                continue
            key = (campaign.campaign_id, day)

            row = {**base, "report": "campaigns", "date": day, **_measures(total)}
            world.add("linkedin_ads_campaigns", [{**row, "campaign_name": campaign.name}])
            world.add_raw("linkedin_ads___ad_analytics", [row])
            for (creative_id, status, serving), part in zip(
                creatives, parts(rng, total, [0.57, 0.43], *key, "creatives"), strict=True
            ):
                raw: Row = {k: v for k, v in base.items() if k != "campaign_id"}
                raw |= {"report": "creatives", "date": day, "creative_id": creative_id, **_measures(part)}
                world.add_raw("linkedin_ads___ad_analytics_creatives", [raw])
                world.add(
                    "linkedin_ads_creatives",
                    [
                        {
                            **raw,
                            "campaign_id": campaign.campaign_id,
                            "campaign_name": campaign.name,
                            "creative_status": status,
                            "is_serving": serving,
                        }
                    ],
                )
        budget = Decimal(int(campaign.impressions * campaign.ctr * campaign.cpc * 1.2))
        world.add_raw(
            "linkedin_ads___campaigns",
            [
                {
                    "source": "linkedin_ads",
                    "id": campaign.campaign_id,
                    "account_id": campaign.account_id,
                    "campaign_group_id": "700001",
                    "name": campaign.name,
                    "status": "ACTIVE",
                    "type": "SPONSORED_UPDATES",
                    "objective_type": "LEAD_GENERATION",
                    "cost_type": "CPC",
                    "optimization_target_type": "MAX_CLICK",
                    "daily_budget": budget,
                    "unit_cost": Decimal(str(campaign.cpc)),
                    "currency": campaign.currency,
                    "run_start": datetime.combine(campaign.start, datetime.min.time()),
                    "run_end": datetime(2026, 12, 31),
                    "created_at": created,
                    "modified_at": created,
                }
            ],
        )
        world.add(
            "linkedin_ads_campaign_list",
            [
                {
                    "source": "linkedin_ads",
                    "account_id": campaign.account_id,
                    "currency": campaign.currency,
                    "campaign_id": campaign.campaign_id,
                    "campaign_name": campaign.name,
                    "daily_budget": budget,
                }
            ],
        )
        for creative_id, status, serving in creatives:
            world.add_raw(
                "linkedin_ads___creatives",
                [
                    {
                        "source": "linkedin_ads",
                        "id": creative_id,
                        "account_id": campaign.account_id,
                        "campaign_id": campaign.campaign_id,
                        "intended_status": status,
                        "is_serving": serving,
                        "content_reference": f"urn:li:share:{creative_id}",
                        "created_at": created,
                        "modified_at": created,
                    }
                ],
            )
