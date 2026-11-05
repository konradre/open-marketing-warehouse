"""From a ``MetricRequest`` to one ClickHouse ``SELECT``.

Single report: aggregate the view at the requested dimensions. Several reports (a cross-source derived
measure, or metrics from two sources): one CTE per report, each aggregated to the shared dimensions
first, then a FULL JOIN on them, then the derived arithmetic. Refusals happen here, before any SQL runs:
a dimension a metric lacks, a non-additive metric away from its own grain, a dimension or filter that
exists on only one side of a cross-source join.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from omw.metrics.moods import ClarifyError, RefuseError
from omw.metrics.request import MetricRequest, TimeRange
from omw.metrics.schema import CAMPAIGN_KEY, PERIOD, TIME_DIMENSION, Catalog, Metric, Report

CAMPAIGN_KEYS_TABLE = "marts.campaign_keys"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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

    @property
    def columns(self) -> list[str]:
        cols = list(self.request.dimensions)
        if self.request.compare:
            cols.append(PERIOD)
        return [*cols, *self.output_metrics]


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise RefuseError(f"{name!r} is not a valid column name")
    return name


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def _filter_clause(name: str, value: Any) -> str:
    if isinstance(value, list):
        if not value:
            raise RefuseError(f"filter on {name} has no values")
        return f"{_ident(name)} IN ({', '.join(_literal(v) for v in value)})"
    return f"{_ident(name)} = {_literal(value)}"


def _resolve_metrics(catalog: Catalog, names: list[str]) -> list[str]:
    resolved: list[str] = []
    for name in names:
        options = catalog.resolve(name)
        if not options:
            raise RefuseError(f"unknown metric {name!r}; call list_metrics")
        if len(options) > 1:
            candidates = []
            for option in options:
                item: Any = catalog.metrics.get(option) or catalog.derived[option]
                candidates.append({"name": option, "description": item.description})
            raise ClarifyError(f"{name!r} can mean {len(options)} metrics; ask for one by name", candidates)
        resolved.append(options[0])
    return resolved


def _check_dimensions(catalog: Catalog, metric: Metric, dimensions: list[str]) -> None:
    report = catalog.report_of(metric)
    allowed = {TIME_DIMENSION, *metric.grains}
    for dimension in dimensions:
        if dimension not in report.all_dimensions:
            raise RefuseError(f"{metric.name} ({report.key}) has no dimension {dimension!r}")
        if dimension not in allowed:
            raise RefuseError(f"{metric.name} is not available at grain {dimension!r}")
    if metric.aggregation == "native":
        native = {TIME_DIMENSION, *metric.grains}
        if set(dimensions) != native:
            raise RefuseError(
                f"{metric.name} is not additive: it can only be asked at its own grain "
                f"{sorted(native)}, not summed across {sorted(native - set(dimensions))}"
            )


def _check_filters(catalog: Catalog, reports: list[Report], filters: dict[str, Any]) -> None:
    for name in filters:
        for report in reports:
            if name not in report.all_dimensions:
                if len(reports) > 1:
                    raise RefuseError(
                        f"filter on {name!r} exists on only one side of the cross-source join "
                        f"({report.key} lacks it)"
                    )
                raise RefuseError(f"{report.key} has no dimension {name!r} to filter on")


def _period_expr(current: TimeRange, previous: TimeRange | None) -> str:
    if previous is None:
        return ""
    window = f"{TIME_DIMENSION} BETWEEN '{current.start}' AND '{current.end}'"
    return f"if({window}, 'current', 'previous') AS {PERIOD}"


def _where(report: Report, current: TimeRange, previous: TimeRange | None, filters: dict[str, Any]) -> str:
    ranges = [f"({TIME_DIMENSION} BETWEEN '{current.start}' AND '{current.end}')"]
    if previous is not None:
        ranges.append(f"({TIME_DIMENSION} BETWEEN '{previous.start}' AND '{previous.end}')")
    clauses = [f"report = '{report.name}'", "(" + " OR ".join(ranges) + ")"]
    clauses += [_filter_clause(name, value) for name, value in filters.items()]
    return " AND ".join(clauses)


def _campaign_key_select(report: Report) -> str:
    """The campaign key: the mapping table's value when present, else the report's own derivation."""
    assert report.campaign_key is not None
    return f"if(ck.campaign_key != '', ck.campaign_key, {report.campaign_key}) AS {CAMPAIGN_KEY}"


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


def _select_dims(report: Report, dimensions: list[str]) -> list[str]:
    parts = []
    for dimension in dimensions:
        if dimension == CAMPAIGN_KEY and report.campaign_key and report.campaign_key != CAMPAIGN_KEY:
            parts.append(_campaign_key_select(report))
        else:
            parts.append(_ident(dimension))
    return parts


def _group_by(dimensions: list[str], period: bool) -> str:
    names = [*dimensions, *([PERIOD] if period else [])]
    return f" GROUP BY {', '.join(names)}" if names else ""


def _report_query(
    report: Report,
    metrics: list[Metric],
    dimensions: list[str],
    current: TimeRange,
    previous: TimeRange | None,
    filters: dict[str, Any],
) -> str:
    select = _select_dims(report, dimensions)
    period = _period_expr(current, previous)
    if period:
        select.append(period)
    select += [f"{metric.expr} AS {metric.name}" for metric in metrics]
    needs_map = CAMPAIGN_KEY in dimensions and report.campaign_key not in (None, CAMPAIGN_KEY)
    join = _campaign_key_join(report) if needs_map else ""
    return (
        f"SELECT {', '.join(select)} FROM {report.view}{join} "
        f"WHERE {_where(report, current, previous, filters)}"
        f"{_group_by(dimensions, previous is not None)}"
    )


def _guard_divisions(expression: str) -> str:
    """Wrap every divisor (a name or a parenthesised group) in ``nullIf(..., 0)``."""
    out = []
    i = 0
    while i < len(expression):
        char = expression[i]
        if char != "/":
            out.append(char)
            i += 1
            continue
        j = i + 1
        while j < len(expression) and expression[j] == " ":
            j += 1
        if j < len(expression) and expression[j] == "(":
            depth, k = 0, j
            while k < len(expression):
                depth += expression[k] == "("
                depth -= expression[k] == ")"
                k += 1
                if depth == 0:
                    break
        else:
            k = j
            while k < len(expression) and (expression[k].isalnum() or expression[k] in "_."):
                k += 1
            if k < len(expression) and expression[k] == "(":  # a function call: include its arguments
                depth = 0
                while k < len(expression):
                    depth += expression[k] == "("
                    depth -= expression[k] == ")"
                    k += 1
                    if depth == 0:
                        break
        out.append(f"/ nullIf({expression[j:k]}, 0)")
        i = k
    return "".join(out)


def _derived_sql(catalog: Catalog, name: str) -> str:
    """The derived expression over Float64 column references, every division guarded against zero."""
    derived = catalog.derived[name]
    expression = derived.derived
    for operand in derived.operands:
        if operand in catalog.derived:
            replacement = f"({_derived_sql(catalog, operand)})"
        else:
            replacement = f"toFloat64({operand})"
        expression = re.sub(rf"(?<![a-z0-9_]){operand}(?![a-z0-9_])", replacement, expression)
    return _guard_divisions(expression)


def _output_select(catalog: Catalog, requested: list[str]) -> list[str]:
    """Output columns in the requested order: a base metric by name, a derived one by its expression."""
    return [
        name if name in catalog.metrics else f"{_derived_sql(catalog, name)} AS {name}" for name in requested
    ]


def compile_request(catalog: Catalog, request: MetricRequest, today: date | None = None) -> Plan:
    today = today or date.today()
    requested = _resolve_metrics(catalog, request.metrics)
    derived = [name for name in requested if name in catalog.derived]
    base: list[Metric] = []
    for name in requested:
        for metric in catalog.base_metrics(name):
            if metric not in base:
                base.append(metric)

    dimensions = [_ident(d) for d in request.dimensions]
    for name in derived:
        common = set(catalog.derived[name].requires_common_grain)
        outside = [d for d in dimensions if d not in common]
        if outside:
            raise RefuseError(
                f"{name} is a cross-source measure; it can only be asked at {sorted(common)}, "
                f"not by {outside}"
            )
        for key in request.filters:
            if key not in common:
                raise RefuseError(
                    f"{name} cannot be filtered by {key!r}: it is not part of its common grain "
                    f"{sorted(common)}"
                )
    by_report: dict[str, list[Metric]] = {}
    for metric in base:
        by_report.setdefault(metric.report_key, []).append(metric)
    reports = [catalog.reports[key] for key in by_report]
    if len(reports) > 1:
        for dimension in dimensions:
            lacking = [r.key for r in reports if dimension not in r.all_dimensions]
            if lacking:
                raise RefuseError(
                    f"dimension {dimension!r} exists on only one side of the cross-source join "
                    f"({lacking} lack it)"
                )
    _check_filters(catalog, reports, request.filters)
    for metric in base:
        _check_dimensions(catalog, metric, dimensions)

    current = request.window(today)
    previous = current.shifted(request.compare) if request.compare else None
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
            select = [*keys, *_output_select(catalog, requested)]
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
        select = [*keys, *_output_select(catalog, requested)]
        sql = f"WITH {ctes} SELECT {', '.join(select)} FROM {joins} ORDER BY {order} LIMIT {request.limit}"

    caveats = _caveats(catalog, base, reports, request, current, today)
    return Plan(request, sql, plans, derived, output, caveats)


def _caveats(
    catalog: Catalog,
    base: list[Metric],
    reports: list[Report],
    request: MetricRequest,
    current: TimeRange,
    today: date,
) -> list[str]:
    found: list[str] = []
    end = current.end
    for report in reports:
        if report.restatement_days:
            boundary = date.fromordinal(today.toordinal() - report.restatement_days)
            if end >= boundary:
                found.append(
                    f"{report.key}: days after {boundary} are inside the source's "
                    f"{report.restatement_days}-day "
                    "restatement window and may still change"
                )
        found.extend(f"{report.key}: {trap}" for trap in report.traps)
    for metric in base:
        if metric.approximate:
            found.append(f"{metric.name} is an approximate distinct count")
        has_currency = "currency" in catalog.report_of(metric).dimensions
        pinned = "currency" in request.dimensions or "currency" in request.filters
        if metric.unit == "currency" and has_currency and not pinned:
            found.append(f"{metric.name} mixes currencies; add the currency dimension or filter to one")
    return list(dict.fromkeys(found))
