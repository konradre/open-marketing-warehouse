"""What the ad clicks turn into: leads, CRM deals and paying customers.

This is the part that makes the seven sources agree with each other. A lead-gen campaign's conversions are
the lead forms Meta and LinkedIn list; a share of those leads become people and opportunities in Twenty
with the campaign's key; a sales campaign's conversions are sign-ups, half of which become paying Stripe
customers whose metadata carries the same key; a deal won in Twenty becomes a Stripe customer too. Organic
customers and inbound deals carry no key at all, as in a real account.

Everything here is the settled truth, independent of the day a snapshot is taken: the builders show the
part of it that had happened by then.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from omw.demo.world import CAMPAIGNS, WEEKDAY, AdCampaign, Calendar, Day, delivery, instant
from omw.eval.generate.rng import Rng

PAID_SHARE = 0.5
"""Sign-ups a platform counts as conversions that go on to pay."""
QUALIFIED_SHARE = 0.22
"""Leads sales accepts into the CRM."""
OPPORTUNITY_SHARE = 0.5
STAGES = ("NEW", "SCREENING", "MEETING", "PROPOSAL", "CUSTOMER")
WON, LOST = "CUSTOMER", "LOST"
PLANS: tuple[tuple[str, int, float], ...] = (
    ("starter", 4900, 0.6),
    ("team", 19900, 0.3),
    ("business", 49900, 0.1),
)
"""(plan, monthly price in minor units, share of self-serve sign-ups)."""

ADJECTIVES = ("Amber", "Boreal", "Cobalt", "Delta", "Ember", "Fathom", "Granite", "Harbor", "Indigo",
              "Juniper", "Kestrel", "Lumen", "Meridian", "Nimbus", "Orchard", "Pioneer")  # fmt: skip
NOUNS = ("Robotics", "Logistics", "Foods", "Labs", "Freight", "Energy", "Health", "Studios", "Supply")
FIRST = ("Ada", "Ben", "Chloe", "Dev", "Elif", "Farah", "Goran", "Hana", "Ivo", "Jun", "Kira", "Luis")
LAST = ("Arden", "Baptiste", "Castell", "Dimov", "Eze", "Fontaine", "Grieve", "Haas", "Ibarra")


@dataclass(frozen=True)
class Lead:
    number: int
    """Its place among the leads of its platform, oldest first."""
    at: datetime
    campaign: AdCampaign


@dataclass(frozen=True)
class Deal:
    number: int
    key: str
    """The campaign key of the lead it came from; empty for an inbound deal."""
    utm_source: str
    utm_medium: str
    company: str
    domain: str
    first_name: str
    last_name: str
    person_at: datetime
    attribution_on: str
    """``person`` or ``company``: the record that carries the attribution fields."""
    opened_at: datetime | None
    """When the opportunity was created; ``None`` for a contact that never became one."""
    stages: tuple[tuple[datetime, str], ...]
    """Every stage the opportunity entered and when, ``NEW`` first."""
    seats: int
    currency: str
    removed_at: datetime | None = None
    """A duplicate, soft-deleted by a rep."""

    @property
    def annual_micros(self) -> int:
        return self.seats * 480 * 1_000_000

    def stage_at(self, when: datetime) -> tuple[datetime, str] | None:
        reached = [step for step in self.stages if step[0] < when]
        return reached[-1] if reached else None

    @property
    def won_at(self) -> datetime | None:
        return next((when for when, stage in self.stages if stage == WON), None)


@dataclass(frozen=True)
class Signup:
    number: int
    at: datetime
    key: str
    utm_source: str
    utm_medium: str
    currency: str
    plan: str
    unit_amount: int
    quantity: int
    name: str
    via: str
    """``self_serve`` (checkout on the site) or ``sales`` (a deal won in the CRM)."""

    @property
    def attributed(self) -> bool:
        return bool(self.key)


@dataclass
class Funnel:
    calendar: Calendar
    days: dict[tuple[str, date], Day] = field(default_factory=dict)
    """(campaign id, day) -> the settled delivery of that campaign day."""
    leads: dict[str, list[Lead]] = field(default_factory=dict)
    """source -> its leads, oldest first."""
    deals: list[Deal] = field(default_factory=list)
    signups: list[Signup] = field(default_factory=list)

    def clicks(self, campaign: AdCampaign, day: date) -> int:
        found = self.days.get((campaign.campaign_id, day))
        return found.clicks if found else 0

    def signups_on(self, day: date) -> list[Signup]:
        return [s for s in self.signups if s.at.date() == day]


def _moment(rng: Rng, day: date, *keys: object) -> datetime:
    """A time of day in working hours, to the second."""
    return instant(day, rng.integer(7 * 3600, 21 * 3600, *keys))


def _deal(rng: Rng, number: int, at: datetime, lead: Lead | None, horizon: datetime) -> Deal:
    key = ("deal", number)
    company = f"{rng.pick(ADJECTIVES, *key, 'adj')} {rng.pick(NOUNS, *key, 'noun')} {number:03d}"
    first, last = rng.pick(FIRST, *key, "first"), rng.pick(LAST, *key, "last")
    opened = None
    stages: list[tuple[datetime, str]] = []
    if rng.chance(OPPORTUNITY_SHARE, *key, "opportunity"):
        opened = at + timedelta(days=rng.integer(1, 6, *key, "open"), minutes=rng.integer(0, 300, *key, "m"))
        when = opened
        stages.append((when, STAGES[0]))
        for index, stage in enumerate(STAGES[1:]):
            when += timedelta(
                days=rng.integer(3, 9, *key, stage), minutes=rng.integer(0, 240, *key, stage, "m")
            )
            if rng.chance(0.2, *key, stage, "lost"):
                stages.append((when, LOST))
                break
            if not rng.chance(0.86 - 0.05 * index, *key, stage, "advance"):
                break  # the deal stalls where it is
            stages.append((when, stage))
    removed = None
    if opened is not None and len(stages) == 1 and rng.chance(0.12, *key, "duplicate"):
        removed = opened + timedelta(days=rng.integer(1, 4, *key, "removed"), hours=3)
    campaign = lead.campaign if lead else None
    return Deal(
        number=number,
        key=campaign.key if campaign else "",
        utm_source=campaign.utm_source if campaign else "",
        utm_medium=campaign.utm_medium if campaign else "",
        company=company,
        domain=f"{company.lower().replace(' ', '-')}.example",
        first_name=first,
        last_name=last,
        person_at=at,
        attribution_on=rng.pick(("person", "company"), *key, "carrier"),
        opened_at=opened if opened is not None and opened < horizon else None,
        stages=tuple(step for step in stages if step[0] < horizon),
        seats=rng.pick((10, 20, 30, 50, 80, 120), *key, "seats"),
        currency="EUR" if rng.chance(0.2, *key, "eur") else "USD",
        removed_at=removed if removed is not None and removed < horizon else None,
    )


@dataclass
class _Numbers:
    """Running sequence numbers, so every record has a stable synthetic id."""

    leads: dict[str, int] = field(default_factory=lambda: {"meta_ads": 0, "linkedin_ads": 0})
    deals: int = 0
    signups: int = 0

    def signup(self) -> int:
        self.signups += 1
        return self.signups

    def deal(self) -> int:
        self.deals += 1
        return self.deals


def build_funnel(rng: Rng, calendar: Calendar) -> Funnel:
    funnel = Funnel(calendar)
    horizon = instant(calendar.today)
    numbers = _Numbers()
    for day in calendar.span("restated"):
        for campaign in CAMPAIGNS:
            final = delivery(rng, calendar, campaign, day)
            if final is None:
                continue
            funnel.days[(campaign.campaign_id, day)] = final
            scope = (campaign.source, campaign.campaign_id, day)
            if campaign.goal == "leads":
                _leads(rng, funnel, campaign, day, final.conversions, numbers, horizon)
                continue
            paying = sum(rng.chance(PAID_SHARE, *scope, "pays", n) for n in range(final.conversions))
            for n in range(paying):
                funnel.signups.append(_self_serve(rng, numbers.signup(), day, campaign, n))
        organic = int(2.6 * WEEKDAY[day.weekday()] * rng.between(0.4, 1.6, "organic", day))
        for n in range(organic):
            funnel.signups.append(_self_serve(rng, numbers.signup(), day, None, n))
        if rng.chance(0.3, "inbound", day):
            funnel.deals.append(_deal(rng, numbers.deal(), _moment(rng, day, "inbound"), None, horizon))
    for deal in list(funnel.deals):
        won = deal.won_at
        if won is not None and won + timedelta(hours=2) < horizon:
            funnel.signups.append(_from_deal(numbers.signup(), deal, won + timedelta(hours=2)))
    funnel.signups.sort(key=lambda signup: (signup.at, signup.number))
    funnel.deals.sort(key=lambda deal: (deal.person_at, deal.number))
    return funnel


def _leads(
    rng: Rng,
    funnel: Funnel,
    campaign: AdCampaign,
    day: date,
    count: int,
    numbers: _Numbers,
    horizon: datetime,
) -> None:
    for n in range(count):
        numbers.leads[campaign.source] += 1
        number = numbers.leads[campaign.source]
        lead = Lead(number, _moment(rng, day, campaign.campaign_id, "lead", n), campaign)
        funnel.leads.setdefault(campaign.source, []).append(lead)
        if rng.chance(QUALIFIED_SHARE, campaign.source, "qualified", number):
            accepted = lead.at + timedelta(hours=rng.integer(2, 40, campaign.source, "accept", number))
            if accepted < horizon:
                funnel.deals.append(_deal(rng, numbers.deal(), accepted, lead, horizon))


def _self_serve(rng: Rng, number: int, day: date, campaign: AdCampaign | None, n: int) -> Signup:
    scope = ("signup", campaign.campaign_id if campaign else "organic", day, n)
    plan, amount = rng.weighted([((name, price), share) for name, price, share in PLANS], *scope, "plan")
    return Signup(
        number=number,
        at=_moment(rng, day, *scope),
        key=campaign.key if campaign else "",
        utm_source=campaign.utm_source if campaign else "",
        utm_medium=campaign.utm_medium if campaign else "",
        currency=campaign.currency if campaign else ("EUR" if rng.chance(0.15, *scope, "eur") else "USD"),
        plan=plan,
        unit_amount=amount,
        quantity=1,
        name=f"Customer {number:04d}",
        via="self_serve",
    )


def _from_deal(number: int, deal: Deal, at: datetime) -> Signup:
    return Signup(
        number=number,
        at=at,
        key=deal.key,
        utm_source=deal.utm_source,
        utm_medium=deal.utm_medium,
        currency=deal.currency,
        plan="business",
        unit_amount=4000,
        quantity=deal.seats,
        name=deal.company,
        via="sales",
    )
