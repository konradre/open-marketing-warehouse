"""From a ``MetricRequest`` to one ClickHouse ``SELECT``.

Single report: aggregate the view at the requested dimensions. Several reports (a cross-source derived
measure, or metrics from two sources): one CTE per report, each aggregated to the shared dimensions
first, then a FULL JOIN on them, then the derived arithmetic. What a request may ask for is decided in
``omw.metrics.rules`` before any SQL is built: refusals first, then the questions back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from omw.metrics.arithmetic import output_select
from omw.metrics.request import Comparison, MetricRequest, TimeRange
from omw.metrics.rules import (
    check_currency,
    check_derived,
    check_dimensions,
    check_filters,
    check_shared_dimensions,
    check_time,
    ident,
    resolve_metrics,
)
from omw.metrics.schema import CAMPAIGN_KEY, PERIOD, TIME_DIMENSION, Catalog, Metric, Report

CAMPAIGN_KEYS_TABLE = "marts.campaign_keys"


@dataclass
class ReportPlan:
    report: Report
    metrics: list[Metric]
    cte: str

    @property
    def alias(self) -> str:
        return f"r_{self.report.source}_{self.report.name}"


@dataclass
class Plan:
    request: MetricRequest
    sql: str
    reports: list[ReportPlan]
    derived: list[str]
    output_metrics: list[str]
    caveats: list[str] = field(default_factory=list)
    comparison: Comparison | None = None
    """The two windows a ``compare`` request covers, stated with the answer."""

    @property
    def columns(self) -> list[str]:
        cols = list(self.request.dimensions)
        if self.request.compare:
            cols.append(PERIOD)
        return [*cols, *self.output_metrics]


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def _filter_clause(column: str, value: Any) -> str:
    if isinstance(value, list):
        return f"{column} IN ({', '.join(_literal(v) for v in value)})"
    return f"{column} = {_literal(value)}"


def _between(window: TimeRange) -> str:
    return f"{TIME_DIMENSION} BETWEEN '{window.start}' AND '{window.end}'"


def _period_expr(current: TimeRange, previous: TimeRange | None) -> str:
    """The ``period`` column. Windows that share days (``week`` on a longer range) put a shared day in both
    periods, one row each, so neither total loses it."""
    if previous is None:
        return ""
    if current.overlaps(previous):
        return (
            f"arrayJoin(arrayConcat(if({_between(current)}, ['current'], []), "
            f"if({_between(previous)}, ['previous'], []))) AS {PERIOD}"
        )
    return f"if({_between(current)}, 'current', 'previous') AS {PERIOD}"


def _campaign_key_expr(report: Report) -> str:
    """The campaign key: the mapping table's value when present, else the report's own derivation."""
    assert report.campaign_key is not None
    return f"if(ck.campaign_key != '', ck.campaign_key, {report.campaign_key})"


def _campaign_key_join(report: Report) -> str:
    """Join the latest mapping row per native id (the table replaces rows only on merge), so a re-synced
    or manually overridden key never fans the report out."""
    native_id = _native_id(report)
    latest = (
        f"SELECT native_id, argMax(campaign_key, updated_at) AS campaign_key FROM {CAMPAIGN_KEYS_TABLE} "
        f"WHERE source = '{report.source}' GROUP BY native_id"
    )
    return f" LEFT JOIN ({latest}) AS ck ON ck.native_id = toString({native_id})"


def _native_id(report: Report) -> str:
    """The column the mapping table keys on: campaign_id where the report has one, else its own key."""
    return "campaign_id" if "campaign_id" in report.dimensions else CAMPAIGN_KEY


def _where(
    report: Report, current: TimeRange | None, previous: TimeRange | None, filters: dict[str, Any]
) -> str:
    """Every row condition of one report. A filter on ``campaign_key`` reads the same expression the
    dimension does: a view whose key comes from ``marts.campaign_keys`` has no such column to filter on."""
    clauses: list[str] = []
    if current is not None:  # a snapshot has neither a report column nor dates
        ranges = [f"({_between(window)})" for window in (current, previous) if window is not None]
        clauses += [f"report = '{report.name}'", "(" + " OR ".join(ranges) + ")"]
    for name, value in filters.items():
        mapped = name == CAMPAIGN_KEY and report.maps_campaign_key
        clauses.append(_filter_clause(_campaign_key_expr(report) if mapped else ident(name), value))
    return f" WHERE {' AND '.join(clauses)}" if clauses else ""


def _select_dims(report: Report, dimensions: list[str]) -> list[str]:
    parts = []
    for dimension in dimensions:
        if dimension == CAMPAIGN_KEY and report.maps_campaign_key:
            parts.append(f"{_campaign_key_expr(report)} AS {CAMPAIGN_KEY}")
        else:
            parts.append(ident(dimension))
    return parts


def _group_by(dimensions: list[str], period: bool) -> str:
    names = [*dimensions, *([PERIOD] if period else [])]
    return f" GROUP BY {', '.join(names)}" if names else ""


def _report_query(
    report: Report,
    metrics: list[Metric],
    dimensions: list[str],
    current: TimeRange | None,
    previous: TimeRange | None,
    filters: dict[str, Any],
) -> str:
    select = _select_dims(report, dimensions)
    period = _period_expr(current, previous) if current is not None else ""
    if period:
        select.append(period)
    select += [f"{metric.expr} AS {metric.name}" for metric in metrics]
    needs_map = report.maps_campaign_key and (CAMPAIGN_KEY in dimensions or CAMPAIGN_KEY in filters)
    join = _campaign_key_join(report) if needs_map else ""
    return (
        f"SELECT {', '.join(select)} FROM {report.view}{join}"
        f"{_where(report, current, previous, filters)}"
        f"{_group_by(dimensions, previous is not None)}"
    )


def compile_request(catalog: Catalog, request: MetricRequest, today: date | None = None) -> Plan:
    today = today or date.today()
    requested = resolve_metrics(catalog, request.metrics)
    derived = [name for name in requested if name in catalog.derived]
    base: list[Metric] = []
    for name in requested:
        for metric in catalog.base_metrics(name):
            if metric not in base:
                base.append(metric)

    dimensions = [ident(d) for d in request.dimensions]
    check_derived(catalog, derived, dimensions, request.filters)
    by_report: dict[str, list[Metric]] = {}
    for metric in base:
        by_report.setdefault(metric.report_key, []).append(metric)
    reports = [catalog.reports[key] for key in by_report]
    check_shared_dimensions(reports, dimensions)
    check_filters(reports, request.filters)
    for metric in base:
        check_dimensions(catalog, metric, dimensions)
    current, previous, comparison = check_time(reports, request, today)
    check_currency(catalog, requested, request)  # the one question back, after every refusal

    plans = [
        ReportPlan(
            report,
            by_report[report.key],
            _report_query(report, by_report[report.key], dimensions, current, previous, request.filters),
        )
        for report in reports
    ]
    keys = [*dimensions, *([PERIOD] if previous else [])]
    output = list(requested)
    order = ", ".join(keys) if keys else "1"

    if len(plans) == 1:
        plan = plans[0]
        if derived:
            select = [*keys, *output_select(catalog, requested)]
            sql = (
                f"WITH {plan.alias} AS ({plan.cte}) SELECT {', '.join(select)} FROM {plan.alias} "
                f"ORDER BY {order} LIMIT {request.limit}"
            )
        else:
            sql = f"{plan.cte} ORDER BY {order} LIMIT {request.limit}"
    else:
        ctes = ", ".join(f"{p.alias} AS ({p.cte})" for p in plans)
        joins = plans[0].alias
        for plan in plans[1:]:
            using = f" USING ({', '.join(keys)})" if keys else " ON 1 = 1"
            joins += f" FULL OUTER JOIN {plan.alias}{using}"
        select = [*keys, *output_select(catalog, requested)]
        sql = f"WITH {ctes} SELECT {', '.join(select)} FROM {joins} ORDER BY {order} LIMIT {request.limit}"

    caveats = _caveats(base, reports, request, current, comparison, today)
    return Plan(request, sql, plans, derived, output, caveats, comparison)


def _caveats(
    base: list[Metric],
    reports: list[Report],
    request: MetricRequest,
    current: TimeRange | None,
    comparison: Comparison | None,
    today: date,
) -> list[str]:
    found: list[str] = []
    for report in reports:
        if report.restatement_days and current is not None:
            boundary = date.fromordinal(today.toordinal() - report.restatement_days)
            if current.end >= boundary:
                found.append(
                    f"{report.key}: days after {boundary} are inside the source's "
                    f"{report.restatement_days}-day "
                    "restatement window and may still change"
                )
        if report.snapshot:
            ignored = "; the time_range was not applied" if request.time_range is not None else ""
            found.append(
                f"{report.key} is the state at the last sync, not a history: it has no dates{ignored}"
            )
        found.extend(f"{report.key}: {trap}" for trap in report.traps)
    for metric in base:
        if metric.approximate:
            found.append(f"{metric.name} is an approximate distinct count")
        if metric.aggregation == "distinct" and request.dimensions:
            found.append(
                f"{metric.name} is a distinct count, recounted for each row: the rows do not add up to "
                "the total"
            )
    found.extend(_comparison_caveats(comparison))
    return list(dict.fromkeys(found))


def _comparison_caveats(comparison: Comparison | None) -> list[str]:
    if comparison is None:
        return []
    current, previous = comparison.current, comparison.previous
    if comparison.overlap_days:
        return [
            f"the compared windows share {comparison.overlap_days} days ({current.start} to {previous.end}); "
            "those days count in both periods"
        ]
    months = comparison.compare in ("month", "year")
    whole = TimeRange(start=current.start, end=current.end).whole_months
    if months and not whole and previous.days != current.days:
        return [
            f"the previous window ({previous.start} to {previous.end}) has {_days(previous.days)} against "
            f"{_days(current.days)}: the earlier month is shorter"
        ]
    return []


def _days(count: int) -> str:
    return f"{count} day" if count == 1 else f"{count} days"
