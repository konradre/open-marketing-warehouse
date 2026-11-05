"""Row-count sanity: the current window's rows of a source's core grain against the previous window's.
A collapse to zero fails; a swing beyond the tolerance warns (seasonality is normal, silence is not)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from omw.checks.results import CheckResult, Status, Warehouse

WINDOW_DAYS = 7
MAX_RATIO = 3.0
MIN_RATIO = 1 / 3


@dataclass(frozen=True)
class RowcountRule:
    source: str
    view: str
    report: str


ROWCOUNT_RULES: tuple[RowcountRule, ...] = (
    RowcountRule("search_console", "marts.search_console_core", "core"),
    RowcountRule("ga4", "marts.ga4_core", "core"),
    RowcountRule("google_ads", "marts.google_ads_campaigns", "campaigns"),
    RowcountRule("meta_ads", "marts.meta_ads_campaigns", "campaigns"),
    RowcountRule("stripe", "marts.stripe_revenue", "invoices"),
    RowcountRule("linkedin_ads", "marts.linkedin_ads_campaigns", "campaigns"),
)


def rowcount_sql(rule: RowcountRule, today: date, window_days: int = WINDOW_DAYS) -> str:
    current_end = today - timedelta(days=1)
    current_start = current_end - timedelta(days=window_days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=window_days - 1)
    return (
        f"SELECT countIf(date BETWEEN '{current_start}' AND '{current_end}') AS current_rows, "
        f"countIf(date BETWEEN '{previous_start}' AND '{previous_end}') AS previous_rows FROM {rule.view}"
    )


def rowcount_checks(
    client: Warehouse,
    today: date,
    rules: tuple[RowcountRule, ...] = ROWCOUNT_RULES,
    existing_views: set[str] | None = None,
    window_days: int = WINDOW_DAYS,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for rule in rules:
        if existing_views is not None and rule.view not in existing_views:
            continue
        current, previous = client.query(rowcount_sql(rule, today, window_days)).result_rows[0]
        current, previous = int(current), int(previous)
        ratio = current / previous if previous else (1.0 if current else 0.0)
        if previous and current == 0:
            status, detail = Status.FAIL, f"0 rows in the last {window_days} days after {previous} before"
        elif not previous and not current:
            status, detail = Status.WARN, f"no rows in the last {2 * window_days} days"
        elif previous and not MIN_RATIO <= ratio <= MAX_RATIO:
            status, detail = Status.WARN, f"{current} rows vs {previous} the window before (x{ratio:.2f})"
        else:
            status, detail = Status.PASS, f"{current} rows vs {previous} the window before"
        results.append(
            CheckResult(rule.source, "rowcount", rule.report, status, float(current), float(previous), detail)
        )
    return results
