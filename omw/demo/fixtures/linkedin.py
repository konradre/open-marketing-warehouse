"""LinkedIn Ads as the Marketing API answers: the account, campaign groups, campaigns, creatives, daily
``adAnalytics`` at the campaign and the creative pivot, and the lead-gen form responses."""

from __future__ import annotations

from datetime import date
from typing import Any

from omw.demo.fixtures.common import Payloads, epoch_ms, money, parts
from omw.demo.funnel import Funnel
from omw.demo.world import COMPANY, LINKEDIN_ACCOUNT, AdCampaign, Day, instant, of_source, reported
from omw.eval.generate.rng import Rng

ACCOUNT_URN = f"urn:li:sponsoredAccount:{LINKEDIN_ACCOUNT}"
GROUPS = {"700001": "Always-on", "700002": "Account-based"}
CREATIVE_SHARES = (0.6, 0.4)


def group_of(campaign: AdCampaign) -> str:
    return "700002" if campaign.manual_key else "700001"


def creatives_of(campaign: AdCampaign) -> list[str]:
    return [f"9{campaign.campaign_id[1:]}{n}" for n in (1, 2)]


def _paged(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {"elements": elements, "paging": {"start": 0, "count": len(elements), "links": []}}


def _element(day: date, urn: str, part: Day) -> dict[str, Any]:
    stamp = {"year": day.year, "month": day.month, "day": day.day}
    return {
        "dateRange": {"start": stamp, "end": stamp},
        "pivotValues": [urn],
        "impressions": part.impressions,
        "clicks": part.clicks,
        "costInLocalCurrency": money(part.cents),
        "externalWebsiteConversions": part.conversions,
        "landingPageClicks": int(part.clicks * 0.84),
        "likes": part.clicks // 9,
        "shares": part.clicks // 31,
        "follows": part.clicks // 47,
    }


def build(rng: Rng, funnel: Funnel, as_of: date) -> Payloads:
    calendar = funnel.calendar
    created = epoch_ms(instant(calendar.start, -12 * 86400))
    audit = {"created": {"time": created}, "lastModified": {"time": created}}
    account = {
        "id": int(LINKEDIN_ACCOUNT),
        "name": COMPANY,
        "status": "ACTIVE",
        "type": "BUSINESS",
        "currency": "USD",
        "test": False,
        "reference": "urn:li:organization:9000001",
        "servingStatuses": ["RUNNABLE"],
        "changeAuditStamps": audit,
    }
    groups = [
        {
            "id": int(group_id),
            "account": ACCOUNT_URN,
            "name": name,
            "status": "ACTIVE",
            "totalBudget": {"amount": "60000", "currencyCode": "USD"},
            "dailyBudget": {"amount": "700" if group_id == "700001" else "250", "currencyCode": "USD"},
            "budgetOptimizationEnabled": group_id == "700001",
            "objectiveType": "WEBSITE_CONVERSIONS" if group_id == "700001" else "LEAD_GENERATION",
            "runSchedule": {"start": created},
            "changeAuditStamps": audit,
        }
        for group_id, name in GROUPS.items()
    ]
    campaigns, creatives, by_campaign, by_creative, leads = [], [], [], [], []
    for campaign in of_source("linkedin_ads"):
        urn = f"urn:li:sponsoredCampaign:{campaign.campaign_id}"
        start = epoch_ms(instant(calendar.day_at(campaign.launch)))
        budget = int(campaign.impressions * campaign.ctr * campaign.cpc * 1.2)
        campaigns.append(
            {
                "id": int(campaign.campaign_id),
                "account": ACCOUNT_URN,
                "campaignGroup": f"urn:li:sponsoredCampaignGroup:{group_of(campaign)}",
                "name": campaign.name,
                "status": "ACTIVE",
                "type": "SPONSORED_UPDATES",
                "objectiveType": "LEAD_GENERATION" if campaign.goal == "leads" else "WEBSITE_CONVERSIONS",
                "costType": "CPC",
                "optimizationTargetType": "MAX_LEAD" if campaign.goal == "leads" else "MAX_CONVERSION",
                "dailyBudget": {"amount": str(budget), "currencyCode": "USD"},
                "unitCost": {"amount": f"{campaign.cpc:.2f}", "currencyCode": "USD"},
                "runSchedule": {"start": start},
                "changeAuditStamps": audit,
            }
        )
        ids = creatives_of(campaign)
        for creative_id in ids:
            creatives.append(
                {
                    "id": f"urn:li:sponsoredCreative:{creative_id}",
                    "account": ACCOUNT_URN,
                    "campaign": urn,
                    "intendedStatus": "ACTIVE",
                    "isServing": True,
                    "content": {"reference": f"urn:li:share:70000000000{creative_id}"},
                    "createdAt": created,
                    "lastModifiedAt": created,
                }
            )
        for day in calendar.span():
            total = reported(rng, calendar, campaign, day, as_of)
            if total is None or not total.impressions:
                continue
            by_campaign.append(_element(day, urn, total))
            shares = parts(rng, total, CREATIVE_SHARES, campaign.campaign_id, day, "creatives")
            for creative_id, part in zip(ids, shares, strict=True):
                if part.impressions:
                    by_creative.append(_element(day, f"urn:li:sponsoredCreative:{creative_id}", part))
    horizon = instant(as_of)
    for lead in funnel.leads.get("linkedin_ads", []):
        if lead.at >= horizon:
            continue
        creative = creatives_of(lead.campaign)[lead.number % 2]
        answers = [f"lead{lead.number}@example.test", f"Lead {lead.number}"]
        leads.append(
            {
                "id": f"urn:li:leadFormResponse:{3000000 + lead.number}",
                "versionedLeadGenFormUrn": "urn:li:versionedLeadGenForm:(urn:li:leadGenForm:6100001,3)",
                "owner": {"sponsoredAccount": ACCOUNT_URN},
                "leadType": "SPONSORED",
                "submittedAt": epoch_ms(lead.at),
                "associatedEntity": {
                    "associatedEntityType": "SPONSORED",
                    "campaign": f"urn:li:sponsoredCampaign:{lead.campaign.campaign_id}",
                    "creative": f"urn:li:sponsoredCreative:{creative}",
                },
                "formResponse": {
                    "answers": [
                        {"questionId": n, "answerDetails": {"textQuestionAnswer": {"answer": answer}}}
                        for n, answer in enumerate(answers, start=1)
                    ]
                },
            }
        )
    return {
        "ad_accounts": _paged([account]),
        "campaign_groups": _paged(groups),
        "campaigns": _paged(campaigns),
        "creatives": _paged(creatives),
        "ad_analytics": _paged(by_campaign),
        "ad_analytics_creatives": _paged(by_creative),
        "lead_form_responses": _paged(leads),
    }
