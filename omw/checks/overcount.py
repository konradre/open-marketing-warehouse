"""Over-count: a breakdown grain may undercount its ``core`` (anonymised queries, GA4's ``(other)``, ad-level
rows for live ad sets only) but never overcount it by more than ``tolerance`` on any group. Every pair is
checked on two measures (a volume and a second one), each as its own result row."""

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
    measures: tuple[str, ...]
    """Additive columns present in both views, each compared on every group."""


OVERCOUNT_RULES: tuple[OvercountRule, ...] = (
    OvercountRule(
        "search_console",
        "marts.search_console_core",
        "marts.search_console_queries",
        "queries",
        ("site_url", "search_type", "date"),
        ("clicks", "impressions"),
    ),
    OvercountRule(
        "search_console",
        "marts.search_console_core",
        "marts.search_console_pages",
        "pages",
        ("site_url", "search_type", "date"),
        ("clicks", "impressions"),
    ),
    OvercountRule(
        "search_console",
        "marts.search_console_core",
        "marts.search_console_query_page",
        "query_page",
        ("site_url", "search_type", "date"),
        ("clicks", "impressions"),
    ),
    OvercountRule(
        "search_console",
        "marts.search_console_core",
        "marts.search_console_countries",
        "countries",
        ("site_url", "search_type", "date"),
        ("clicks", "impressions"),
    ),
    OvercountRule(
        "search_console",
        "marts.search_console_core",
        "marts.search_console_devices",
        "devices",
        ("site_url", "search_type", "date"),
        ("clicks", "impressions"),
    ),
    OvercountRule(
        "ga4",
        "marts.ga4_core",
        "marts.ga4_channels",
        "channels",
        ("property_id", "date"),
        ("sessions", "new_users"),
    ),
    OvercountRule(
        "ga4",
        "marts.ga4_core",
        "marts.ga4_landing_pages",
        "landing_pages",
        ("property_id", "date"),
        ("sessions", "new_users"),
    ),
    OvercountRule(
        "ga4",
        "marts.ga4_core",
        "marts.ga4_campaigns",
        "campaigns",
        ("property_id", "date"),
        ("sessions", "new_users"),
    ),
    OvercountRule(
        "google_ads",
        "marts.google_ads_campaigns",
        "marts.google_ads_ad_groups",
        "ad_groups",
        ("customer_id", "campaign_id", "date"),
        ("cost_micros", "clicks"),
    ),
    OvercountRule(
        "google_ads",
        "marts.google_ads_campaigns",
        "marts.google_ads_search_terms",
        "search_terms",
        ("customer_id", "campaign_id", "date"),
        ("cost_micros", "clicks"),
    ),
    OvercountRule(
        "meta_ads",
        "marts.meta_ads_campaigns",
        "marts.meta_ads_ads",
        "ads",
        ("account_id", "campaign_id", "date"),
        ("spend", "clicks"),
    ),
    OvercountRule(
        "meta_ads",
        "marts.meta_ads_campaigns",
        "marts.meta_ads_age_gender",
        "age_gender",
        ("account_id", "campaign_id", "date"),
        ("spend", "clicks"),
    ),
    OvercountRule(
        "meta_ads",
        "marts.meta_ads_campaigns",
        "marts.meta_ads_countries",
        "countries",
        ("account_id", "campaign_id", "date"),
        ("spend", "clicks"),
    ),
    OvercountRule(
        "meta_ads",
        "marts.meta_ads_campaigns",
        "marts.meta_ads_placements",
        "placements",
        ("account_id", "campaign_id", "date"),
        ("spend", "clicks"),
    ),
)


def overcount_sql(rule: OvercountRule, since: date, tolerance: float = TOLERANCE) -> str:
    """One row: the group count, then per measure its violating groups and its worst breakdown/core ratio."""
    keys = ", ".join(rule.keys)
    core_sums = ", ".join(f"sum({m}) AS core_{m}" for m in rule.measures)
    breakdown_sums = ", ".join(f"sum({m}) AS breakdown_{m}" for m in rule.measures)
    per_measure = ", ".join(
        f"countIf(breakdown_{m} > core_{m} * {1 + tolerance}) AS violations_{m}, "
        f"max(breakdown_{m} / nullIf(core_{m}, 0)) AS ratio_{m}"
        for m in rule.measures
    )
    return (
        f"WITH core AS (SELECT {keys}, {core_sums} FROM {rule.core} "
        f"WHERE date >= '{since}' GROUP BY {keys}), "
        f"breakdown AS (SELECT {keys}, {breakdown_sums} FROM {rule.breakdown} "
        f"WHERE date >= '{since}' GROUP BY {keys}) "
        f"SELECT count() AS groups, {per_measure} "
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
        groups, *per_measure = client.query(overcount_sql(rule, since, tolerance)).result_rows[0]
        violations = dict(zip(rule.measures, per_measure[0::2], strict=True))
        ratio = max((float(r or 0.0) for r in per_measure[1::2]), default=0.0)
        failing = [(m, int(v)) for m, v in violations.items() if v]
        if groups == 0:
            status, detail = Status.WARN, f"no {rule.report} rows since {since}"
        elif failing:
            status = Status.FAIL
            detail = "; ".join(
                f"{v} of {groups} groups exceed core by more than {tolerance:.0%} on {m}" for m, v in failing
            )
        else:
            status = Status.PASS
            detail = f"{groups} groups within {tolerance:.0%} of core on {', '.join(rule.measures)}"
        results.append(
            CheckResult(rule.source, "overcount", rule.report, status, ratio, 1 + tolerance, detail)
        )
    return results
