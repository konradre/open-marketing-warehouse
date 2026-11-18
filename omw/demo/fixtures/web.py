"""Organic search and site traffic: Search Console's six grains and GA4's four.

Search Console: ``core`` is the property total. Pages, countries and devices add up to it; queries do not,
because Google withholds anonymised queries, so the named queries cover about two thirds of the clicks. The
freshest two days are still filling in.

GA4: the ``campaigns`` grain is built first, one row per campaign, source and medium. Paid rows come from
the ad platforms' clicks and carry the campaign key as the campaign name; the organic row follows Search
Console's clicks; the long tail of small campaigns is folded into one ``(other)`` row, as GA4 does past its
cardinality limit. ``core`` is the sum of those rows, ``channels`` regroups them and ``landing_pages``
shares the total out again with its own ``(other)`` row, so no breakdown exceeds ``core``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from omw.demo.fixtures.common import Payloads, share
from omw.demo.funnel import Funnel
from omw.demo.world import CAMPAIGNS, EUR_USD, SITE_HOST, WEEKDAY, AdCampaign, Calendar
from omw.eval.generate.rng import Rng

OTHER = "(other)"
QUERIES: tuple[tuple[str, float, float, str], ...] = (
    # (query, share of the named clicks, typical position, landing path)
    ("example widgets", 16.0, 1.2, "/"),
    ("example widgets pricing", 7.0, 1.4, "/pricing"),
    ("example widgets login", 6.0, 1.1, "/"),
    ("widget software", 5.5, 7.8, "/widgets"),
    ("best widget tool", 4.5, 9.4, "/blog/best-widget-tools"),
    ("widget management", 4.0, 8.1, "/widgets"),
    ("widgets for teams", 3.5, 6.3, "/widgets"),
    ("gadget tracking software", 3.0, 11.2, "/gadgets"),
    ("how to organise widgets", 3.0, 5.6, "/blog/organise-widgets"),
    ("widget inventory template", 2.8, 4.9, "/templates/widget-inventory"),
    ("widget software pricing", 2.5, 8.8, "/pricing"),
    ("widget vs gadget", 2.2, 3.7, "/blog/widget-vs-gadget"),
    ("gadgets launch", 2.0, 6.0, "/gadgets"),
    ("widget ebook", 1.8, 4.2, "/ebook"),
    ("widget api", 1.6, 5.1, "/docs/api"),
    ("widget integrations", 1.5, 9.9, "/integrations"),
    ("small business widget tool", 1.2, 14.6, "/widgets"),
    ("widget reporting", 1.1, 12.3, "/blog/widget-reporting"),
    ("free widget tracker", 1.0, 17.5, "/templates/widget-inventory"),
    ("widget software europe", 0.9, 10.4, "/widgets"),
)
PAGES: tuple[tuple[str, float], ...] = (
    ("/", 26.0), ("/pricing", 11.0), ("/widgets", 16.0), ("/gadgets", 7.0), ("/ebook", 3.0),
    ("/blog/best-widget-tools", 8.0), ("/blog/organise-widgets", 6.0), ("/blog/widget-vs-gadget", 5.0),
    ("/blog/widget-reporting", 3.0), ("/templates/widget-inventory", 6.0), ("/docs/api", 4.0),
    ("/integrations", 3.0), ("/about", 2.0),
)  # fmt: skip
COUNTRIES = (("usa", 0.46), ("gbr", 0.14), ("deu", 0.11), ("can", 0.08), ("fra", 0.06), ("ind", 0.05),
             ("aus", 0.04), ("nld", 0.03), ("esp", 0.03))  # fmt: skip
DEVICES = (("DESKTOP", 0.58), ("MOBILE", 0.38), ("TABLET", 0.04))
SEARCH_FILLS = (0.6, 0.9)
"""The share of a day Search Console has one and two days later."""
GA4_FILLS = (0.88, 0.97)

GA4_METRICS = ("sessions", "totalUsers", "newUsers", "engagedSessions", "screenPageViews", "keyEvents",
               "totalRevenue")  # fmt: skip
ADDITIVE = ("sessions", "new_users", "engaged", "views", "events", "revenue_cents")
LANDING: tuple[tuple[str, float], ...] = (*PAGES[:9], ("/signup", 9.0), ("/demo", 4.0), (OTHER, 6.0))
CHANNEL_OF_KEY = {"widgets_retargeting": "Display", "gadgets_launch": "Cross-network"}


def url(path: str) -> str:
    return f"https://{SITE_HOST}{path}"


def _fill(steps: tuple[float, ...], day: date, as_of: date) -> float:
    age = (as_of - day).days
    return steps[age - 1] if age <= len(steps) else 1.0


def organic_clicks(rng: Rng, calendar: Calendar, day: date) -> tuple[int, int, float]:
    """(clicks, impressions, average position) of the property on a settled day."""
    growth = 1 + 0.0015 * (day - calendar.start).days
    clicks = int(880 * WEEKDAY[day.weekday()] * growth * rng.between(0.9, 1.1, "search", day))
    impressions = int(clicks / rng.between(0.029, 0.036, "search", day, "ctr"))
    return clicks, impressions, rng.between(13.5, 16.5, "search", day, "position")


def _search_row(keys: list[str], clicks: int, impressions: int, position: float) -> dict[str, Any]:
    clicks = min(clicks, impressions)
    return {
        "keys": keys,
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(clicks / impressions, 6) if impressions else 0.0,
        "position": round(position, 2),
    }


def build_search_console(rng: Rng, funnel: Funnel, as_of: date) -> Payloads:
    calendar = funnel.calendar
    rows: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("core", "queries", "pages", "query_page", "countries", "devices")
    }
    for day in calendar.span():
        if day >= as_of:
            continue
        clicks, impressions, position = organic_clicks(rng, calendar, day)
        fill = _fill(SEARCH_FILLS, day, as_of)
        clicks, impressions = int(clicks * fill), int(impressions * fill)
        iso = day.isoformat()
        rows["core"].append(_search_row([iso], clicks, impressions, position))
        for name, members in (("pages", PAGES), ("countries", COUNTRIES), ("devices", DEVICES)):
            weights = rng.jitter([w for _, w in members], 0.2, "search", day, name)
            pieces = zip(share(clicks, weights), share(impressions, weights), strict=True)
            for (member, _), (part_clicks, part_impressions) in zip(members, pieces, strict=True):
                if part_impressions:
                    key = url(member) if name == "pages" else member
                    shift = rng.between(-2.0, 2.0, "search", day, name, member)
                    rows[name].append(
                        _search_row([iso, key], part_clicks, part_impressions, position + shift)
                    )
        # Named queries: the rest of the property total belongs to queries Google does not disclose.
        named_clicks = int(clicks * rng.between(0.64, 0.72, "search", day, "named"))
        named_impressions = int(impressions * rng.between(0.56, 0.66, "search", day, "named-impressions"))
        weights = rng.jitter([w for _, w, *_ in QUERIES], 0.25, "search", day, "queries")
        pieces = zip(share(named_clicks, weights), share(named_impressions, weights), strict=True)
        for (query, _, rank, path), (part_clicks, part_impressions) in zip(QUERIES, pieces, strict=True):
            if not part_impressions:
                continue
            place = rank * rng.between(0.9, 1.15, "search", day, query)
            row = _search_row([iso, query], part_clicks, part_impressions, place)
            rows["queries"].append(row)
            # Most of a query lands on one page; a tenth of it on the home page.
            side = row["clicks"] // 10 if path != "/" else 0
            side_impressions = part_impressions // 8 if path != "/" else 0
            main = _search_row(
                [iso, query, url(path)], row["clicks"] - side, part_impressions - side_impressions, place
            )
            rows["query_page"].append(main)
            if side_impressions:
                rows["query_page"].append(
                    _search_row([iso, query, url("/")], side, side_impressions, place + 3)
                )
    return {name: {"rows": found, "responseAggregationType": "byProperty"} for name, found in rows.items()}


@dataclass
class Traffic:
    """One GA4 campaign-grain row before formatting: additive measures only."""

    campaign: str
    source: str
    medium: str
    channel: str
    sessions: int
    new_users: int = 0
    engaged: int = 0
    views: int = 0
    events: int = 0
    revenue_cents: int = 0


def _traffic(campaign: str, source: str, medium: str, channel: str, sessions: int, new: float, engaged: float,
             depth: float) -> Traffic:  # fmt: skip
    return Traffic(campaign, source, medium, channel, sessions, int(sessions * new), int(sessions * engaged),
                   int(sessions * depth))  # fmt: skip


def _paid_channel(campaign: AdCampaign) -> str:
    if campaign.utm_medium == "paid_social":
        return "Paid Social"
    return CHANNEL_OF_KEY.get(campaign.key, "Paid Search")


def traffic_on(rng: Rng, funnel: Funnel, day: date) -> list[Traffic]:
    """Every GA4 campaign row of a settled day, the ``(other)`` row included."""
    calendar = funnel.calendar
    found: list[Traffic] = []
    for campaign in CAMPAIGNS:
        clicks = funnel.clicks(campaign, day)
        if not clicks:
            continue
        landed = int(clicks * rng.between(0.78, 0.9, "ga4", campaign.campaign_id, day))
        found.append(
            _traffic(
                campaign.key,
                campaign.utm_source,
                campaign.utm_medium,
                _paid_channel(campaign),
                landed,
                0.72,
                0.55,
                2.2,
            )  # fmt: skip
        )
    organic, _, _ = organic_clicks(rng, calendar, day)
    level = WEEKDAY[day.weekday()]
    found += [
        _traffic("(organic)", "google", "organic", "Organic Search", int(organic * 0.93), 0.58, 0.68, 3.1),
        _traffic("(organic)", "bing", "organic", "Organic Search", int(organic * 0.06), 0.61, 0.66, 2.9),
        _traffic("(direct)", "(direct)", "(none)", "Direct",
                 int(430 * level * rng.between(0.9, 1.1, "ga4", "direct", day)), 0.35, 0.62, 3.4),
        _traffic("(referral)", "partner-directory.example", "referral", "Referral",
                 int(64 * level * rng.between(0.8, 1.2, "ga4", "referral", day)), 0.66, 0.6, 2.7),
        _traffic("newsletter_weekly", "newsletter", "email", "Email",
                 int((260 if day.weekday() == 1 else 35) * rng.between(0.85, 1.15, "ga4", "email", day)),
                 0.12, 0.71, 2.5),
    ]  # fmt: skip
    tail = int(sum(row.sessions for row in found) * rng.between(0.03, 0.05, "ga4", "tail", day))
    found.append(_traffic(OTHER, OTHER, OTHER, "Unassigned", tail, 0.6, 0.4, 1.8))
    _conversions(funnel, day, found)
    return found


def _conversions(funnel: Funnel, day: date, rows: list[Traffic]) -> None:
    """Key events and purchase revenue: each sign-up lands on the row of its campaign and source; an
    unattributed one on organic search or direct."""
    by_campaign = {(row.campaign, row.source): row for row in rows}
    fallback = [row for row in rows if row.channel in ("Organic Search", "Direct")]
    for signup in funnel.signups_on(day):
        row = by_campaign.get((signup.key, signup.utm_source)) if signup.key else None
        row = row or fallback[signup.number % len(fallback)]
        rate = EUR_USD if signup.currency == "EUR" else 1.0
        row.events += 1
        row.revenue_cents += int(signup.unit_amount * signup.quantity * rate)


def _ga4_row(day: date, dimensions: list[str], sums: dict[str, int], users_share: float) -> dict[str, Any]:
    values = [
        sums["sessions"],
        int(sums["sessions"] * users_share),
        sums["new_users"],
        sums["engaged"],
        sums["views"],
        sums["events"],
        round(sums["revenue_cents"] / 100, 2),
    ]
    return {
        "dimensionValues": [{"value": day.strftime("%Y%m%d")}, *({"value": value} for value in dimensions)],
        "metricValues": [{"value": str(value)} for value in values],
    }


def _report(dimensions: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = dict.fromkeys(GA4_METRICS, "TYPE_INTEGER") | {
        "keyEvents": "TYPE_FLOAT",
        "totalRevenue": "TYPE_CURRENCY",
    }
    return {
        "dimensionHeaders": [{"name": name} for name in dimensions],
        "metricHeaders": [{"name": name, "type": kind} for name, kind in kinds.items()],
        "rows": rows,
        "rowCount": len(rows),
        "metadata": {"currencyCode": "USD", "timeZone": "Etc/UTC"},
        "kind": "analyticsData#runReport",
    }


def build_ga4(rng: Rng, funnel: Funnel, as_of: date) -> Payloads:
    rows: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("core", "channels", "landing_pages", "campaigns")
    }
    for day in funnel.calendar.span():
        if day >= as_of:
            continue
        fill = _fill(GA4_FILLS, day, as_of)
        traffic = traffic_on(rng, funnel, day)
        seen = [{name: int(getattr(row, name) * fill) for name in ADDITIVE} for row in traffic]
        for row, sums in zip(traffic, seen, strict=True):
            if sums["sessions"]:
                dims = [row.campaign, row.source, row.medium]
                rows["campaigns"].append(_ga4_row(day, dims, sums, 0.87))
        total = {name: sum(sums[name] for sums in seen) for name in ADDITIVE}
        rows["core"].append(_ga4_row(day, [], total, 0.81))
        for channel in dict.fromkeys(row.channel for row in traffic):
            members = [sums for row, sums in zip(traffic, seen, strict=True) if row.channel == channel]
            grouped = {name: sum(sums[name] for sums in members) for name in ADDITIVE}
            if grouped["sessions"]:
                rows["channels"].append(_ga4_row(day, [channel], grouped, 0.85))
        weights = rng.jitter([w for _, w in LANDING], 0.15, "ga4", "landing", day)
        pieces = {name: share(total[name], weights) for name in ADDITIVE}
        for index, (path, _) in enumerate(LANDING):
            part = {name: pieces[name][index] for name in ADDITIVE}
            if part["sessions"]:
                rows["landing_pages"].append(_ga4_row(day, [path], part, 0.88))
    dimensions = {
        "core": ["date"],
        "channels": ["date", "sessionDefaultChannelGroup"],
        "landing_pages": ["date", "landingPage"],
        "campaigns": ["date", "sessionCampaignName", "sessionSource", "sessionMedium"],
    }
    return {name: _report(dimensions[name], found) for name, found in rows.items()}
