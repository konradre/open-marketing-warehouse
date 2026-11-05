"""Over-count: a breakdown grain may undercount its ``core`` (anonymised queries, GA4's ``(other)``, ad-level
rows for live ad sets only) but never overcount it by more than ``tolerance`` on any group."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from omw.checks.results import CheckResult, Status, Warehouse

TOLERANCE = 0.01
WINDOW_DAYS = 28


@dataclass(frozen=True)
class OvercountRule:
    source: str
    core: str
    """The reference view."""
    breakdown: str
    """The breakdown view that must not exceed it."""
    report: str
    """The breakdown's report name, for the result row."""
    keys: tuple[str, ...]
    """Columns both views share, including ``date``, that define a group."""
    measure: str
    """An additive column present in both views."""


OVERCOUNT_RULES: tuple[OvercountRule, ...] = (
    OvercountRule(
        "search_console",
        "marts.search_console_core",
        "marts.search_console_queries",
        "queries",
        ("site_url", "search_type", "date"),
        "clicks",
    ),
    OvercountRule(
        "search_console",
        "marts.search_console_core",
        "marts.search_console_pages",
        "pages",
        ("site_url", "search_type", "date"),
        "clicks",
    ),
    OvercountRule(
        "search_console",
        "marts.search_console_core",
        "marts.search_console_query_page",
        "query_page",
        ("site_url", "search_type", "date"),
        "clicks",
    ),
    OvercountRule(
        "search_console",
        "marts.search_console_core",
        "marts.search_console_countries",
        "countries",
        ("site_url", "search_type", "date"),
        "clicks",
    ),
    OvercountRule(
        "search_console",
        "marts.search_console_core",
        "marts.search_console_devices",
        "devices",
        ("site_url", "search_type", "date"),
        "clicks",
    ),
    OvercountRule(
        "ga4", "marts.ga4_core", "marts.ga4_channels", "channels", ("property_id", "date"), "sessions"
    ),
    OvercountRule(
        "ga4",
        "marts.ga4_core",
        "marts.ga4_landing_pages",
        "landing_pages",
        ("property_id", "date"),
        "sessions",
    ),
    OvercountRule(
        "ga4", "marts.ga4_core", "marts.ga4_campaigns", "campaigns", ("property_id", "date"), "sessions"
    ),
    OvercountRule(
        "google_ads",
        "marts.google_ads_campaigns",
        "marts.google_ads_ad_groups",
        "ad_groups",
        ("customer_id", "campaign_id", "date"),
        "cost_micros",
    ),
    OvercountRule(
        "meta_ads",
        "marts.meta_ads_campaigns",
        "marts.meta_ads_ads",
        "ads",
        ("account_id", "campaign_id", "date"),
        "spend",
    ),
)


def overcount_sql(rule: OvercountRule, since: date, tolerance: float = TOLERANCE) -> str:
    keys = ", ".join(rule.keys)
    return (
        f"WITH core AS (SELECT {keys}, sum({rule.measure}) AS core_total FROM {rule.core} "
        f"WHERE date >= '{since}' GROUP BY {keys}), "
        f"breakdown AS (SELECT {keys}, sum({rule.measure}) AS breakdown_total FROM {rule.breakdown} "
        f"WHERE date >= '{since}' GROUP BY {keys}) "
        f"SELECT count() AS groups, countIf(breakdown_total > core_total * {1 + tolerance}) AS violations, "
        f"max(breakdown_total / nullIf(core_total, 0)) AS max_ratio "
        f"FROM breakdown LEFT JOIN core USING ({keys})"
    )


def overcount_checks(
    client: Warehouse,
    today: date,
    rules: tuple[OvercountRule, ...] = OVERCOUNT_RULES,
    existing_views: set[str] | None = None,
    window_days: int = WINDOW_DAYS,
    tolerance: float = TOLERANCE,
) -> list[CheckResult]:
    since = today - timedelta(days=window_days)
    results: list[CheckResult] = []
    for rule in rules:
        if existing_views is not None and (
            rule.core not in existing_views or rule.breakdown not in existing_views
        ):
            continue  # the source has not been loaded yet
        groups, violations, max_ratio = client.query(overcount_sql(rule, since, tolerance)).result_rows[0]
        ratio = float(max_ratio or 0.0)
        if groups == 0:
            status, detail = Status.WARN, f"no {rule.report} rows since {since}"
        elif violations:
            status = Status.FAIL
            detail = (
                f"{violations} of {groups} groups exceed core by more than {tolerance:.0%} on {rule.measure}"
            )
        else:
            status, detail = Status.PASS, f"{groups} groups within {tolerance:.0%} of core on {rule.measure}"
        results.append(
            CheckResult(rule.source, "overcount", rule.report, status, ratio, 1 + tolerance, detail)
        )
    return results
