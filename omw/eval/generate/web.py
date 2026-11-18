"""Search Console and GA4: the ``core`` totals and every breakdown grain.

Search Console is built from the queries up: ``core`` is the named queries plus an anonymised remainder
Google never attributes, so the queries grain undercounts ``core`` the way the real one does. GA4 is built
from ``core`` down: each breakdown is a split that includes the ``(other)`` row, so it sums to ``core``.
"""

from __future__ import annotations

from datetime import date

from omw.eval.generate.campaigns import LAUNCH, WEEKDAY
from omw.eval.generate.rng import Rng, split
from omw.eval.world import START, Row, World, days

SITE = "sc-domain:example.com"
PROPERTY = "300000001"
HOME = "https://example.com/"

# (query, impressions on an average day, average position, landing page, optional second page)
WEB_QUERIES: tuple[tuple[str, int, float, str, str], ...] = (
    ("example widgets", 2400, 1.2, "/", "/pricing"),
    ("widgets pricing", 1500, 3.4, "/pricing", ""),
    ("buy widgets online", 2100, 6.8, "/products/widgets", "/pricing"),
    ("best widgets 2026", 1800, 8.1, "/blog/best-widgets", ""),
    ("industrial widgets", 1300, 5.2, "/products/widgets", ""),
    ("widget supplier", 1150, 7.4, "/products/widgets", "/about"),
    ("what is a widget", 2600, 9.6, "/blog/what-is-a-widget", ""),
    ("widgets vs gadgets", 1400, 4.9, "/blog/widgets-vs-gadgets", ""),
    ("gadget launch", 900, 6.1, "/products/gadgets", ""),
    ("example gadgets", 780, 1.6, "/products/gadgets", "/"),
    ("widget installation guide", 1250, 3.8, "/docs/install", ""),
    ("widget api", 640, 2.9, "/docs/api", ""),
    ("widget maintenance", 820, 7.9, "/docs/install", "/blog/what-is-a-widget"),
    ("cheap widgets", 1700, 11.3, "/pricing", ""),
    ("widgets wholesale", 720, 8.8, "/pricing", "/products/widgets"),
    ("example widgets login", 560, 1.1, "/", ""),
    ("widget comparison", 980, 6.4, "/blog/widgets-vs-gadgets", "/blog/best-widgets"),
    ("how to choose a widget", 1100, 10.2, "/blog/best-widgets", ""),
    ("widgets for small business", 860, 9.1, "/products/widgets", ""),
    ("example widgets reviews", 430, 2.3, "/about", ""),
    ("widget ebook", 380, 4.2, "/resources/ebook", ""),
    ("widget buyers guide pdf", 340, 5.6, "/resources/ebook", ""),
    ("widget sizes chart", 690, 5.9, "/docs/install", ""),
    ("example widgets careers", 210, 1.4, "/about", ""),
)
IMAGE_QUERIES: tuple[tuple[str, int, float, str, str], ...] = (
    ("widget diagram", 900, 4.4, "/blog/what-is-a-widget", ""),
    ("widget sizes chart", 620, 3.1, "/docs/install", ""),
    ("example widgets logo", 300, 1.8, "/about", ""),
    ("gadget photo", 540, 7.2, "/products/gadgets", ""),
    ("industrial widgets", 480, 9.5, "/products/widgets", ""),
)
SC_COUNTRIES = (("usa", 0.52), ("gbr", 0.16), ("deu", 0.12), ("can", 0.11), ("ind", 0.09))
SC_DEVICES = (("DESKTOP", 0.49), ("MOBILE", 0.45), ("TABLET", 0.06))

CHANNELS = (
    ("Organic Search", 0.30),
    ("Paid Search", 0.17),
    ("Paid Social", 0.14),
    ("Direct", 0.19),
    ("Referral", 0.07),
    ("Email", 0.06),
    ("Organic Social", 0.05),
    ("(other)", 0.02),
)
# (campaign, source, medium, share of sessions, first day)
GA4_CAMPAIGNS: tuple[tuple[str, str, str, float, date], ...] = (
    ("(organic)", "google", "organic", 0.30, START),
    ("(direct)", "(direct)", "(none)", 0.19, START),
    ("Brand - Search", "google", "cpc", 0.07, START),
    ("Widgets | Prospecting", "google", "cpc", 0.06, START),
    ("Gadgets Launch", "google", "cpc", 0.04, LAUNCH),
    ("widgets_prospecting", "facebook", "paid_social", 0.06, START),
    ("widgets_retargeting", "facebook", "paid_social", 0.03, START),
    ("gadgets_launch", "facebook", "paid_social", 0.03, LAUNCH),
    ("lead_gen_ebook", "linkedin", "paid_social", 0.02, START),
    ("monthly_newsletter", "newsletter", "email", 0.06, START),
    ("(referral)", "partner.example", "referral", 0.07, START),
    ("(not set)", "(not set)", "(not set)", 0.05, START),
    ("(other)", "(other)", "(other)", 0.02, START),
)
LANDING_PAGES = (
    ("/", 0.24),
    ("/pricing", 0.15),
    ("/products/widgets", 0.14),
    ("/products/gadgets", 0.08),
    ("/blog/what-is-a-widget", 0.09),
    ("/blog/best-widgets", 0.07),
    ("/blog/widgets-vs-gadgets", 0.05),
    ("/docs/install", 0.06),
    ("/resources/ebook", 0.04),
    ("/about", 0.03),
    ("(not set)", 0.03),
    ("(other)", 0.02),
)


def _ctr(position: float) -> float:
    """Click-through falls off quickly below the first results."""
    return float(0.42 / (position**1.15))


def _sc_row(base: Row, report: str, clicks: int, impressions: int, position: float, **dims: str) -> Row:
    return {
        **base,
        "report": report,
        **dims,
        "clicks": clicks,
        "impressions": impressions,
        "ctr": clicks / impressions if impressions else 0.0,
        "position": round(position, 4),
    }


def _add_sc(world: World, report: str, row: Row) -> None:
    world.add(f"search_console_{report}", [row])
    world.add_raw(f"search_console___{report}", [row])


def build_search_console(rng: Rng, world: World) -> None:
    for search_type, queries in (("web", WEB_QUERIES), ("image", IMAGE_QUERIES)):
        pages = sorted({page for *_, page, second in queries} | {s for *_, s in queries if s})
        for day in days():
            base: Row = {
                "source": "search_console",
                "site_url": SITE,
                "search_type": search_type,
                "date": day,
            }
            level = WEEKDAY[day.weekday()] * (1 + 0.0009 * (day - START).days)
            total_clicks = total_impressions = 0
            weighted_position = 0.0
            by_page: dict[str, list[int]] = {page: [0, 0] for page in pages}
            for query, volume, rank, page, second in queries:
                key = (search_type, query, day)
                impressions = int(volume * level * rng.between(0.82, 1.18, *key, "impressions"))
                position = max(1.0, rank + rng.between(-0.4, 0.4, *key, "position"))
                clicks = int(impressions * _ctr(position) * rng.between(0.8, 1.2, *key, "clicks"))
                _add_sc(
                    world, "queries", _sc_row(base, "queries", clicks, impressions, position, query=query)
                )
                total_clicks += clicks
                total_impressions += impressions
                weighted_position += position * impressions
                shares = [0.8, 0.2] if second else [1.0]
                for target, c, i in zip(
                    [page, second][: len(shares)],
                    split(clicks, shares),
                    split(impressions, shares),
                    strict=True,
                ):
                    by_page[target][0] += c
                    by_page[target][1] += i
                    row = _sc_row(
                        base, "query_page", c, i, position, query=query, page=HOME.rstrip("/") + target
                    )
                    _add_sc(world, "query_page", row)
            # The anonymised remainder: in core, never in the query grains.
            hidden_impressions = int(
                total_impressions * rng.between(0.30, 0.40, search_type, day, "hidden_i")
            )
            hidden_clicks = int(total_clicks * rng.between(0.24, 0.34, search_type, day, "hidden_c"))
            core_clicks = total_clicks + hidden_clicks
            core_impressions = total_impressions + hidden_impressions
            core_position = (weighted_position + 17.5 * hidden_impressions) / core_impressions
            _add_sc(world, "core", _sc_row(base, "core", core_clicks, core_impressions, core_position))

            weights = [1.0 + by_page[page][0] for page in pages]
            extra_clicks = split(int(hidden_clicks * 0.9), weights)
            extra_impressions = split(int(hidden_impressions * 0.9), weights)
            for page, more_clicks, more_impressions in zip(
                pages, extra_clicks, extra_impressions, strict=True
            ):
                clicks, impressions = by_page[page][0] + more_clicks, by_page[page][1] + more_impressions
                position = rng.between(3.0, 9.0, search_type, page, "position")
                row = _sc_row(base, "pages", clicks, impressions, position, page=HOME.rstrip("/") + page)
                _add_sc(world, "pages", row)

            for report, column, members, share in (
                ("countries", "country", SC_COUNTRIES, 0.99),
                ("devices", "device", SC_DEVICES, 1.0),
            ):
                jittered = rng.jitter([w for _, w in members], 0.12, search_type, day, report)
                clicks_split = split(int(core_clicks * share), jittered)
                impressions_split = split(int(core_impressions * share), jittered)
                for (member, _), c, i in zip(members, clicks_split, impressions_split, strict=True):
                    position = core_position + rng.between(-1.5, 1.5, search_type, day, member)
                    _add_sc(world, report, _sc_row(base, report, c, i, position, **{column: member}))


GA4_MEASURES = ("sessions", "new_users", "engaged_sessions", "page_views", "key_events")


def _ga4_split(rng: Rng, core: Row, weights: list[float], *keys: object) -> list[Row]:
    columns = {name: split(int(core[name]), rng.jitter(weights, 0.12, *keys, name)) for name in GA4_MEASURES}
    revenue = split(int(round(core["total_revenue"] * 100)), rng.jitter(weights, 0.2, *keys, "revenue"))
    rows: list[Row] = []
    for index in range(len(weights)):
        row: Row = {name: columns[name][index] for name in GA4_MEASURES}
        row["key_events"] = float(row["key_events"])
        row["total_users"] = int(row["sessions"] * 0.8)
        row["total_revenue"] = revenue[index] / 100
        rows.append(row)
    return rows


def build_ga4(rng: Rng, world: World) -> None:
    for day in days():
        base: Row = {"source": "ga4", "property_id": PROPERTY, "date": day}
        level = WEEKDAY[day.weekday()] * (1 + 0.0011 * (day - START).days)
        sessions = int(5200 * level * rng.between(0.9, 1.1, "ga4", day, "sessions"))
        core: Row = {
            **base,
            "report": "core",
            "is_other": False,
            "sessions": sessions,
            "total_users": int(sessions * 0.78),
            "new_users": int(sessions * rng.between(0.36, 0.46, "ga4", day, "new_users")),
            "engaged_sessions": int(sessions * rng.between(0.54, 0.62, "ga4", day, "engaged")),
            "page_views": int(sessions * rng.between(2.4, 3.0, "ga4", day, "page_views")),
            "key_events": float(int(sessions * rng.between(0.017, 0.025, "ga4", day, "key_events"))),
            "total_revenue": round(sessions * rng.between(1.1, 1.7, "ga4", day, "revenue"), 2),
        }
        world.add("ga4_core", [{k: v for k, v in core.items() if k != "is_other"}])
        world.add_raw("ga4___core", [core])

        parts = _ga4_split(rng, core, [w for _, w in CHANNELS], "ga4", day, "channels")
        for (channel, _), part in zip(CHANNELS, parts, strict=True):
            row = {**base, "report": "channels", "channel_group": channel, "is_other": channel == "(other)"}
            world.add("ga4_channels", [row | part])
            world.add_raw("ga4___channels", [row | part])

        live = [c for c in GA4_CAMPAIGNS if day >= c[4]]
        parts = _ga4_split(rng, core, [c[3] for c in live], "ga4", day, "campaigns")
        for (campaign, source_name, medium, _, _), part in zip(live, parts, strict=True):
            row = {**base, "report": "campaigns", "campaign": campaign, "source_name": source_name}
            row |= {"medium": medium, "is_other": campaign == "(other)"}
            world.add("ga4_campaigns", [row | part])
            world.add_raw("ga4___campaigns", [row | part])

        parts = _ga4_split(rng, core, [w for _, w in LANDING_PAGES], "ga4", day, "landing_pages")
        for (page, _), part in zip(LANDING_PAGES, parts, strict=True):
            row = {**base, "report": "landing_pages", "landing_page": page, "is_other": page == "(other)"}
            world.add("ga4_landing_pages", [row | part])
            world.add_raw("ga4___landing_pages", [row | part])


def build_web(rng: Rng, world: World) -> None:
    build_search_console(rng, world)
    build_ga4(rng, world)
