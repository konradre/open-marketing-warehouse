"""The synthetic company: a calendar that ends yesterday, the ad campaigns and the keys they share.

Every identifier here is made up. The campaign key of a campaign is the slug of its name
(``omw.metrics.campaign_keys.slug``), which is what the ad pipelines derive on their own; GA4 carries it as
``utm_campaign``, Stripe as customer metadata and Twenty as the ``campaignKey`` custom field. One LinkedIn
campaign is named differently on purpose and gets its key by hand, as ``omw campaign-keys set`` would.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from omw.eval.generate.rng import Rng

SEED = 20261101
DEFAULT_DAYS = 90
MIN_DAYS = 45
"""The shortest history the demo loads: the Search Console backfill, Meta's 28-day window and the row-count
check's two weeks all need real days behind them."""

Snapshot = Literal["initial", "restated"]
SNAPSHOTS: tuple[Snapshot, ...] = ("initial", "restated")
"""``initial``: what every API answered yesterday. ``restated``: what it answers today, with one more day
and the recent days revised the way each platform revises them."""

META_ACCOUNT = "100200300400500"
GOOGLE_USD = "4000000001"
GOOGLE_EUR = "4000000002"
GOOGLE_MANAGER = "4000000000"
LINKEDIN_ACCOUNT = "510000001"
GA4_PROPERTY = "400000001"
SITE_URL = "sc-domain:widgets.example"
SITE_HOST = "widgets.example"
TWENTY_URL = "https://crm.widgets.example"
COMPANY = "Example Widgets"
EUR_USD = 1.08
"""The fixed rate GA4 reports EUR purchases at (its property currency is USD)."""

WEEKDAY = (1.06, 1.10, 1.09, 1.05, 0.98, 0.76, 0.72)
"""Monday to Sunday: business traffic dips at the weekend."""


@dataclass(frozen=True)
class Calendar:
    today: date
    days: int = DEFAULT_DAYS

    def __post_init__(self) -> None:
        if self.days < MIN_DAYS:
            raise ValueError(f"the demo needs at least {MIN_DAYS} days of history, got {self.days}")

    @property
    def start(self) -> date:
        """The first day with data."""
        return self.today - timedelta(days=self.days)

    def as_of(self, snapshot: Snapshot) -> date:
        """The day a snapshot was taken on: its data ends the day before."""
        return self.today if snapshot == "restated" else self.today - timedelta(days=1)

    def span(self, snapshot: Snapshot = "restated") -> list[date]:
        """Every complete day a snapshot holds, oldest first."""
        count = (self.as_of(snapshot) - self.start).days
        return [self.start + timedelta(days=n) for n in range(count)]

    def day_at(self, fraction: float) -> date:
        """The day ``fraction`` of the way through the history (0 = first day)."""
        return self.start + timedelta(days=int(self.days * fraction))


def instant(day: date, seconds: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(seconds=seconds)


@dataclass(frozen=True)
class AdCampaign:
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
    cvr: float
    """Conversions per click as the platform attributes them: purchases, or leads for a ``leads`` goal."""
    goal: Literal["sales", "leads"] = "sales"
    launch: float = 0.0
    """How far into the history the campaign starts (0 = from the first day)."""
    trend: float = 0.0
    """Growth per day."""
    channel: str = ""
    """Google's advertising channel type."""
    utm_source: str = ""
    utm_medium: str = ""
    manual_key: bool = False
    """The key is not the slug of the name: it is set by hand in ``marts.campaign_keys``."""


def _meta(campaign_id: str, name: str, key: str, impressions: int, ctr: float, cpc: float, cvr: float,
          **extra: object) -> AdCampaign:  # fmt: skip
    return AdCampaign(
        "meta_ads", META_ACCOUNT, "USD", campaign_id, name, key, impressions, ctr, cpc, cvr,
        utm_source="facebook", utm_medium="paid_social", **extra,  # type: ignore[arg-type]
    )  # fmt: skip


def _google(account: str, currency: str, campaign_id: str, name: str, key: str, impressions: int,
            ctr: float, cpc: float, cvr: float, channel: str, **extra: object) -> AdCampaign:  # fmt: skip
    return AdCampaign(
        "google_ads", account, currency, campaign_id, name, key, impressions, ctr, cpc, cvr,
        channel=channel, utm_source="google", utm_medium="cpc", **extra,  # type: ignore[arg-type]
    )  # fmt: skip


def _linkedin(campaign_id: str, name: str, key: str, impressions: int, ctr: float, cpc: float, cvr: float,
              **extra: object) -> AdCampaign:  # fmt: skip
    return AdCampaign(
        "linkedin_ads", LINKEDIN_ACCOUNT, "USD", campaign_id, name, key, impressions, ctr, cpc, cvr,
        utm_source="linkedin", utm_medium="paid_social", **extra,  # type: ignore[arg-type]
    )  # fmt: skip


CAMPAIGNS: tuple[AdCampaign, ...] = (
    _meta("23850000000000001", "Widgets - Prospecting", "widgets_prospecting", 42000, 0.011, 0.62, 0.0040,
          trend=0.0012),
    _meta("23850000000000002", "Widgets - Retargeting", "widgets_retargeting", 9000, 0.024, 0.48, 0.0075),
    _meta("23850000000000003", "Gadgets Launch", "gadgets_launch", 30000, 0.013, 0.71, 0.0040, launch=0.4),
    _meta("23850000000000004", "Lead Gen - Ebook", "lead_gen_ebook", 12000, 0.017, 0.93, 0.035,
          goal="leads"),
    _google(GOOGLE_USD, "USD", "20010001", "Brand - Search", "brand_search", 6000, 0.085, 0.35, 0.0060,
            "SEARCH"),
    _google(GOOGLE_USD, "USD", "20010002", "Widgets | Prospecting", "widgets_prospecting", 15000, 0.032,
            1.45, 0.0050, "SEARCH", trend=0.001),
    _google(GOOGLE_USD, "USD", "20010003", "Widgets Retargeting", "widgets_retargeting", 50000, 0.004,
            0.55, 0.0060, "DISPLAY"),
    _google(GOOGLE_USD, "USD", "20010004", "Gadgets Launch", "gadgets_launch", 28000, 0.012, 0.80, 0.0045,
            "PERFORMANCE_MAX", launch=0.4),
    _google(GOOGLE_EUR, "EUR", "20020001", "EMEA - Generic", "emea_generic", 9000, 0.030, 1.10, 0.0050,
            "SEARCH"),
    _linkedin("800001", "Widgets Prospecting", "widgets_prospecting", 5000, 0.006, 6.20, 0.020),
    _linkedin("800002", "Gadgets Launch", "gadgets_launch", 4000, 0.007, 5.40, 0.020, launch=0.4),
    _linkedin("800003", "Lead Gen eBook", "lead_gen_ebook", 3500, 0.009, 7.10, 0.11, goal="leads"),
    _linkedin("800004", "LI | ABM Tier 1", "abm_tier_1", 1500, 0.011, 9.50, 0.10, goal="leads",
              manual_key=True),
)  # fmt: skip

CAMPAIGN_KEYS: tuple[str, ...] = tuple(dict.fromkeys(c.key for c in CAMPAIGNS))


def of_source(source: str) -> list[AdCampaign]:
    return [c for c in CAMPAIGNS if c.source == source]


@dataclass(frozen=True)
class Day:
    """What a campaign delivered on one day."""

    impressions: int
    clicks: int
    cents: int
    conversions: int


def delivery(rng: Rng, calendar: Calendar, campaign: AdCampaign, day: date) -> Day | None:
    """The settled numbers of a campaign day: what the platform reports once it has stopped revising."""
    if day < calendar.day_at(campaign.launch) or day < calendar.start:
        return None
    key = (campaign.source, campaign.campaign_id, day)
    age = (day - calendar.start).days
    level = campaign.impressions * WEEKDAY[day.weekday()] * (1 + campaign.trend * age)
    impressions = int(level * rng.between(0.86, 1.14, *key, "impressions"))
    clicks = int(impressions * campaign.ctr * rng.between(0.85, 1.15, *key, "clicks"))
    cents = int(clicks * campaign.cpc * 100 * rng.between(0.9, 1.1, *key, "cost"))
    conversions = _whole(rng, clicks * campaign.cvr * rng.between(0.7, 1.3, *key, "cvr"), *key, "conv")
    return Day(impressions, clicks, cents, conversions)


def _whole(rng: Rng, expected: float, *keys: object) -> int:
    """A whole number with ``expected`` as its mean: small rates still produce the odd conversion."""
    base = int(expected)
    return base + (1 if rng.chance(expected - base, *keys) else 0)


DELIVERY_SETTLES: dict[str, tuple[float, ...]] = {
    # the share of a day's impressions, clicks and cost a platform reports 1, 2, ... days later
    "meta_ads": (0.94, 0.985),
    "google_ads": (0.99,),
    "linkedin_ads": (0.96, 0.99),
}
CONVERSIONS_SETTLE: dict[str, tuple[float, float]] = {
    # (share attributed after one day, share added by each further day): conversions trail the click
    "meta_ads": (0.65, 0.07),
    "google_ads": (0.64, 0.04),
    "linkedin_ads": (0.80, 0.10),
}
"""Every factor reaches 1.0 inside the window the source's daily sync re-fetches (Meta 7 days, Google Ads
30, LinkedIn 7), so after one daily run the warehouse holds exactly the restated snapshot."""


def settled(source: str, age: int) -> tuple[float, float]:
    """(delivery share, conversion share) a platform reports ``age`` days after the day itself."""
    steps = DELIVERY_SETTLES[source]
    first, step = CONVERSIONS_SETTLE[source]
    return (steps[age - 1] if age <= len(steps) else 1.0, min(1.0, round(first + step * (age - 1), 6)))


def reported(rng: Rng, calendar: Calendar, campaign: AdCampaign, day: date, as_of: date) -> Day | None:
    """A campaign day as the platform reports it on ``as_of``: recent days are still short."""
    final = delivery(rng, calendar, campaign, day)
    if final is None or day >= as_of:
        return None
    share, conversions = settled(campaign.source, (as_of - day).days)
    return Day(
        int(final.impressions * share),
        int(final.clicks * share),
        int(final.cents * share),
        int(final.conversions * conversions),
    )
