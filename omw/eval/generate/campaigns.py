"""The ad campaigns of the synthetic company and how much each delivers on a given day.

Campaign totals are drawn here; every breakdown grain is a whole-number split of them (``parts``), so a
grain that should sum to its campaigns does, exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from omw.eval.generate.rng import Rng, split
from omw.eval.world import END, START

WEEKDAY = (1.06, 1.10, 1.09, 1.05, 0.98, 0.76, 0.72)
"""Monday to Sunday: business traffic dips at the weekend."""
LAUNCH = date(2026, 7, 13)
SEPTEMBER = date(2026, 9, 1)

META_ACCOUNT = "100200300400500"
GOOGLE_USD = "4000000001"
GOOGLE_EUR = "4000000002"
LINKEDIN_ACCOUNT = "510000001"


@dataclass(frozen=True)
class Campaign:
    source: str
    account_id: str
    currency: str
    campaign_id: str
    name: str
    key: str
    impressions: int
    """Impressions on an average day."""
    ctr: float
    cpc: float
    cvr: float = 0.0
    start: date = START
    end: date = END
    trend: float = 0.0
    """Growth per day since ``START``."""
    september: float = 1.0
    """A budget change on 1 September, as a multiplier."""
    channel: str = ""
    manual_key: bool = False
    extra: dict[str, str] = field(default_factory=dict)


CAMPAIGNS: tuple[Campaign, ...] = (
    Campaign(
        "meta_ads",
        META_ACCOUNT,
        "USD",
        "23850000000000001",
        "Widgets - Prospecting",
        "widgets_prospecting",
        42000,
        0.011,
        0.62,
        trend=0.0012,
    ),
    Campaign(
        "meta_ads",
        META_ACCOUNT,
        "USD",
        "23850000000000002",
        "Widgets - Retargeting",
        "widgets_retargeting",
        9000,
        0.024,
        0.48,
        september=0.8,
    ),
    Campaign(
        "meta_ads",
        META_ACCOUNT,
        "USD",
        "23850000000000003",
        "Gadgets Launch",
        "gadgets_launch",
        30000,
        0.013,
        0.71,
        start=LAUNCH,
        september=1.35,
    ),
    Campaign(
        "meta_ads",
        META_ACCOUNT,
        "USD",
        "23850000000000004",
        "Lead Gen - Ebook",
        "lead_gen_ebook",
        12000,
        0.017,
        0.93,
    ),
    Campaign(
        "google_ads",
        GOOGLE_USD,
        "USD",
        "20010001",
        "Brand - Search",
        "brand_search",
        6000,
        0.085,
        0.35,
        0.09,
        channel="SEARCH",
    ),
    Campaign(
        "google_ads",
        GOOGLE_USD,
        "USD",
        "20010002",
        "Widgets | Prospecting",
        "widgets_prospecting",
        15000,
        0.032,
        1.45,
        0.035,
        trend=0.001,
        channel="SEARCH",
    ),
    Campaign(
        "google_ads",
        GOOGLE_USD,
        "USD",
        "20010003",
        "Widgets Retargeting",
        "widgets_retargeting",
        50000,
        0.004,
        0.55,
        0.02,
        channel="DISPLAY",
    ),
    Campaign(
        "google_ads",
        GOOGLE_USD,
        "USD",
        "20010004",
        "Gadgets Launch",
        "gadgets_launch",
        28000,
        0.012,
        0.80,
        0.03,
        start=LAUNCH,
        september=1.25,
        channel="PERFORMANCE_MAX",
    ),
    Campaign(
        "google_ads",
        GOOGLE_EUR,
        "EUR",
        "20020001",
        "EMEA - Generic",
        "emea_generic",
        9000,
        0.030,
        1.10,
        0.03,
        channel="SEARCH",
    ),
    Campaign(
        "google_ads",
        GOOGLE_EUR,
        "EUR",
        "20020002",
        "EMEA - Brand",
        "emea_brand",
        2500,
        0.090,
        0.30,
        0.08,
        channel="SEARCH",
    ),
    Campaign(
        "linkedin_ads",
        LINKEDIN_ACCOUNT,
        "USD",
        "800001",
        "Widgets Prospecting",
        "widgets_prospecting",
        5000,
        0.006,
        6.20,
        0.03,
    ),
    Campaign(
        "linkedin_ads",
        LINKEDIN_ACCOUNT,
        "USD",
        "800002",
        "Gadgets Launch",
        "gadgets_launch",
        4000,
        0.007,
        5.40,
        0.03,
        start=LAUNCH,
    ),
    Campaign(
        "linkedin_ads",
        LINKEDIN_ACCOUNT,
        "USD",
        "800003",
        "Lead Gen eBook",
        "lead_gen_ebook",
        3500,
        0.009,
        7.10,
        0.02,
        september=1.2,
    ),
    Campaign(
        "linkedin_ads",
        LINKEDIN_ACCOUNT,
        "USD",
        "800004",
        "LI | ABM Tier 1",
        "abm_tier_1",
        1500,
        0.011,
        9.50,
        0.04,
        manual_key=True,
    ),
)


@dataclass(frozen=True)
class Day:
    impressions: int
    clicks: int
    cents: int
    conversions: int
    """Whole conversions; Google reports them in tenths, so its rows divide this by ten."""


def delivery(rng: Rng, campaign: Campaign, day: date) -> Day | None:
    if not campaign.start <= day <= campaign.end:
        return None
    key = (campaign.source, campaign.campaign_id, day)
    level = campaign.impressions * WEEKDAY[day.weekday()] * (1 + campaign.trend * (day - START).days)
    if day >= SEPTEMBER:
        level *= campaign.september
    impressions = int(level * rng.between(0.86, 1.14, *key, "impressions"))
    clicks = int(impressions * campaign.ctr * rng.between(0.85, 1.15, *key, "clicks"))
    cents = int(clicks * campaign.cpc * 100 * rng.between(0.9, 1.1, *key, "cost"))
    scale = 10 if campaign.source == "google_ads" else 1
    conversions = int(clicks * campaign.cvr * scale * rng.between(0.7, 1.3, *key, "conversions"))
    return Day(impressions, clicks, cents, conversions)


def money(cents: int) -> Decimal:
    return Decimal(cents) / 100


def of_source(source: str) -> list[Campaign]:
    return [c for c in CAMPAIGNS if c.source == source]


def parts(rng: Rng, day_: Day, weights: list[float], *keys: object) -> list[Day]:
    """A campaign day shared out over the members of a breakdown."""
    impressions = split(day_.impressions, rng.jitter(weights, 0.15, *keys, "impressions"))
    clicks = split(day_.clicks, rng.jitter(weights, 0.25, *keys, "clicks"))
    cents = split(day_.cents, rng.jitter(weights, 0.25, *keys, "cost"))
    conversions = split(day_.conversions, rng.jitter(weights, 0.3, *keys, "conversions"))
    return [Day(*values) for values in zip(impressions, clicks, cents, conversions, strict=True)]
