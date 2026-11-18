"""Meta Ads as the Graph API answers: entity lists, daily insights at campaign and ad level, the three
campaign breakdowns and the lead forms. The ad of a paused ad set is left out of the ad level, so that grain
undercounts its campaign, as the real one does."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from omw.demo.fixtures.common import Payloads, iso_offset, money, parts
from omw.demo.funnel import Funnel
from omw.demo.world import COMPANY, META_ACCOUNT, AdCampaign, Day, instant, of_source, reported
from omw.eval.generate.rng import Rng

AGES = (("18-24", 0.14), ("25-34", 0.36), ("35-44", 0.31), ("45-54", 0.19))
GENDERS = (("female", 0.47), ("male", 0.53))
COUNTRIES = (("US", 0.58), ("CA", 0.14), ("GB", 0.17), ("DE", 0.11))
PLACEMENTS = (
    (("facebook", "feed"), 0.41),
    (("instagram", "feed"), 0.27),
    (("instagram", "instagram_stories"), 0.19),
    (("facebook", "facebook_reels"), 0.13),
)
AD_SHARES = (0.5, 0.32, 0.18)
ORDER_VALUE_CENTS = 13900


def ads_of(campaign: AdCampaign) -> list[tuple[str, str, str, str, bool]]:
    """(ad set id, ad set name, ad id, ad name, ad set is live). The third ad sits in a paused ad set."""
    tail = campaign.campaign_id[-2:]
    return [
        (f"2386000000000{tail}1", "Broad", f"2387000000000{tail}1", "Static image", True),
        (f"2386000000000{tail}1", "Broad", f"2387000000000{tail}2", "Carousel", True),
        (f"2386000000000{tail}2", "Lookalike (paused)", f"2387000000000{tail}3", "Video 15s", False),
    ]


def _insight(campaign: AdCampaign, day: date, part: Day, extra: dict[str, str]) -> dict[str, Any]:
    reach = int(part.impressions * 0.64)
    row: dict[str, Any] = {
        "account_id": campaign.account_id,
        "campaign_id": campaign.campaign_id,
        "campaign_name": campaign.name,
        **extra,
        "date_start": day.isoformat(),
        "date_stop": day.isoformat(),
        "impressions": str(part.impressions),
        "clicks": str(part.clicks),
        "reach": str(reach),
        "spend": money(part.cents),
    }
    if reach:
        row["frequency"] = f"{part.impressions / reach:.6f}"
    if part.impressions:
        row["ctr"] = f"{100 * part.clicks / part.impressions:.6f}"
        row["cpm"] = f"{10 * part.cents / part.impressions:.6f}"
    if part.clicks:
        row["cpc"] = f"{part.cents / 100 / part.clicks:.6f}"
        row["actions"] = [{"action_type": "link_click", "value": str(int(part.clicks * 0.93))}]
    if part.conversions:
        kind = "lead" if campaign.goal == "leads" else "purchase"
        row.setdefault("actions", []).append({"action_type": kind, "value": str(part.conversions)})
        if kind == "purchase":
            value = money(part.conversions * ORDER_VALUE_CENTS)
            row["action_values"] = [{"action_type": "purchase", "value": value}]
    return row


def _entities(campaign: AdCampaign, start: date) -> tuple[dict[str, Any], list[dict[str, Any]], list[Any]]:
    created = iso_offset(instant(start - timedelta(days=12), 9 * 3600))
    budget = int(campaign.impressions * campaign.ctr * campaign.cpc * 1.2) * 100
    common = {"account_id": campaign.account_id, "created_time": created, "updated_time": created}
    entity = {
        "id": campaign.campaign_id,
        "name": campaign.name,
        "status": "ACTIVE",
        "effective_status": "ACTIVE",
        "objective": "OUTCOME_LEADS" if campaign.goal == "leads" else "OUTCOME_SALES",
        "start_time": iso_offset(instant(start)),
        "daily_budget": str(budget),
        "buying_type": "AUCTION",
        **common,
    }
    ad_sets: dict[str, dict[str, Any]] = {}
    ads = []
    for adset_id, adset_name, ad_id, ad_name, live in ads_of(campaign):
        status = "ACTIVE" if live else "PAUSED"
        ad_sets[adset_id] = {
            "id": adset_id,
            "campaign_id": campaign.campaign_id,
            "name": adset_name,
            "status": status,
            "effective_status": status,
            "optimization_goal": "LEAD_GENERATION" if campaign.goal == "leads" else "OFFSITE_CONVERSIONS",
            "billing_event": "IMPRESSIONS",
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            **common,
        }
        ads.append(
            {
                "id": ad_id,
                "adset_id": adset_id,
                "campaign_id": campaign.campaign_id,
                "name": ad_name,
                "status": status,
                "effective_status": status,
                "creative": {"id": ad_id.replace("2387", "2388", 1)},
                **common,
            }
        )
    return entity, list(ad_sets.values()), ads


def build(rng: Rng, funnel: Funnel, as_of: date) -> Payloads:
    calendar = funnel.calendar
    insights: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("campaigns", "ads", "age_gender", "countries", "placements")
    }
    campaigns, ad_sets, ads, leads = [], [], [], []
    for campaign in of_source("meta_ads"):
        entity, sets, campaign_ads = _entities(campaign, calendar.day_at(campaign.launch))
        campaigns.append(entity)
        ad_sets += sets
        ads += campaign_ads
        members = ads_of(campaign)
        segments = [(age, gender, wa * wg) for age, wa in AGES for gender, wg in GENDERS]
        for day in calendar.span():
            total = reported(rng, calendar, campaign, day, as_of)
            if total is None:
                continue
            key = (campaign.campaign_id, day)
            insights["campaigns"].append(_insight(campaign, day, total, {}))
            shares = parts(rng, total, [w for *_, w in segments], *key, "age_gender")
            for (age, gender, _), part in zip(segments, shares, strict=True):
                insights["age_gender"].append(_insight(campaign, day, part, {"age": age, "gender": gender}))
            shares = parts(rng, total, [w for _, w in COUNTRIES], *key, "countries")
            for (country, _), part in zip(COUNTRIES, shares, strict=True):
                insights["countries"].append(_insight(campaign, day, part, {"country": country}))
            shares = parts(rng, total, [w for _, w in PLACEMENTS], *key, "placements")
            for ((platform, position), _), part in zip(PLACEMENTS, shares, strict=True):
                extra = {"publisher_platform": platform, "platform_position": position}
                insights["placements"].append(_insight(campaign, day, part, extra))
            shares = parts(rng, total, AD_SHARES, *key, "ads")
            for (adset_id, adset_name, ad_id, ad_name, live), part in zip(members, shares, strict=True):
                if live:
                    extra = {
                        "adset_id": adset_id,
                        "adset_name": adset_name,
                        "ad_id": ad_id,
                        "ad_name": ad_name,
                    }
                    insights["ads"].append(_insight(campaign, day, part, extra))
    horizon = instant(as_of)
    for lead in funnel.leads.get("meta_ads", []):
        if lead.at >= horizon:
            continue
        adset_id, adset_name, ad_id, ad_name, _ = ads_of(lead.campaign)[lead.number % 2]
        leads.append(
            {
                "id": f"2390000000{lead.number:07d}",
                "created_time": iso_offset(lead.at),
                "ad_id": ad_id,
                "ad_name": ad_name,
                "adset_id": adset_id,
                "adset_name": adset_name,
                "campaign_id": lead.campaign.campaign_id,
                "campaign_name": lead.campaign.name,
                "form_id": "4500000000000001",
                "is_organic": False,
                "platform": "ig" if lead.number % 3 == 0 else "fb",
                "field_data": [
                    {"name": "email", "values": [f"lead{lead.number}@example.test"]},
                    {"name": "full_name", "values": [f"Lead {lead.number}"]},
                ],
            }
        )
    account = {
        "id": f"act_{META_ACCOUNT}",
        "account_id": META_ACCOUNT,
        "name": COMPANY,
        "currency": "USD",
        "timezone_name": "Etc/UTC",
    }
    return {
        "account": account,
        "campaigns": {"data": campaigns},
        "ad_sets": {"data": ad_sets},
        "ads": {"data": ads},
        **{f"insights_{name}": {"data": rows} for name, rows in insights.items()},
        "leads": {"data": leads},
    }
