"""What a request may ask for, decided before any SQL exists.

Refusals: an unknown metric, a dimension a metric lacks, a non-additive measure away from its own grain, a
dimension or filter on one side of a cross-source join, a dated measure without dates, a snapshot compared
or mixed with dated measures. Questions back: a name that means several metrics, and an amount of money
with no currency chosen. Refusals are checked first, so a request that has no valid path is never answered
with a question about its currency.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from omw.metrics.moods import ClarifyError, RefuseError
from omw.metrics.request import Comparison, MetricRequest, TimeRange
from omw.metrics.schema import CURRENCY, TIME_DIMENSION, Catalog, Metric, Report

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(name: str) -> str:
    if not _IDENT.match(name):
        raise RefuseError(f"{name!r} is not a valid column name")
    return name


def resolve_metrics(catalog: Catalog, names: list[str]) -> list[str]:
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


def check_derived(
    catalog: Catalog, derived: list[str], dimensions: list[str], filters: dict[str, Any]
) -> None:
    """A cross-source measure only exists at its common grain."""
    for name in derived:
        common = set(catalog.derived[name].requires_common_grain)
        outside = [d for d in dimensions if d not in common]
        if outside:
            raise RefuseError(
                f"{name} is a cross-source measure; it can only be asked at {sorted(common)}, "
                f"not by {outside}"
            )
        for key in filters:
            if key not in common:
                raise RefuseError(
                    f"{name} cannot be filtered by {key!r}: it is not part of its common grain "
                    f"{sorted(common)}"
                )


def check_shared_dimensions(reports: list[Report], dimensions: list[str]) -> None:
    if len(reports) < 2:
        return
    for dimension in dimensions:
        lacking = [r.key for r in reports if dimension not in r.all_dimensions]
        if lacking:
            raise RefuseError(
                f"dimension {dimension!r} exists on only one side of the cross-source join "
                f"({lacking} lack it)"
            )


def check_filters(reports: list[Report], filters: dict[str, Any]) -> None:
    for name, value in filters.items():
        if isinstance(value, list) and not value:
            raise RefuseError(f"filter on {name} has no values")
        for report in reports:
            if name not in report.all_dimensions:
                if len(reports) > 1:
                    raise RefuseError(
                        f"filter on {name!r} exists on only one side of the cross-source join "
                        f"({report.key} lacks it)"
                    )
                raise RefuseError(f"{report.key} has no dimension {name!r} to filter on")


def check_dimensions(catalog: Catalog, metric: Metric, dimensions: list[str]) -> None:
    report = catalog.report_of(metric)
    time = set() if report.snapshot else {TIME_DIMENSION}
    allowed = time | set(metric.grains)
    for dimension in dimensions:
        if dimension not in report.all_dimensions:
            raise RefuseError(f"{metric.name} ({report.key}) has no dimension {dimension!r}")
        if dimension not in allowed:
            raise RefuseError(f"{metric.name} is not available at grain {dimension!r}")
    if metric.aggregation == "native" and set(dimensions) != allowed:
        raise RefuseError(
            f"{metric.name} is not additive: it can only be asked at its own grain "
            f"{sorted(allowed)}, not summed across {sorted(allowed - set(dimensions))}"
        )


def check_time(
    reports: list[Report], request: MetricRequest, today: date
) -> tuple[TimeRange | None, TimeRange | None, Comparison | None]:
    """The current window, the compared one and how they relate; all ``None`` for a snapshot."""
    snapshots = [r.key for r in reports if r.snapshot]
    dated = [r.key for r in reports if not r.snapshot]
    if snapshots and dated:
        raise RefuseError(
            f"{', '.join(snapshots)} is a list as last synced, with no dates, and {', '.join(dated)} is "
            "dated: ask for them separately"
        )
    if snapshots:
        if request.compare:
            raise RefuseError(
                f"{', '.join(snapshots)} is a list as last synced: there is no earlier state to compare with"
            )
        return None, None, None
    current = request.window(today)
    if current is None:
        raise RefuseError(
            "time_range is required for a dated measure: {start, end} as ISO dates or a named range"
        )
    if request.compare is None:
        return current, None, None
    previous = current.shifted(request.compare)
    return current, previous, Comparison.between(request.compare, current, previous)


def currency_pinned(request: MetricRequest) -> bool:
    """True when the request keeps currencies apart: the dimension, or a filter on exactly one currency."""
    if CURRENCY in request.dimensions:
        return True
    value = request.filters.get(CURRENCY)
    return value is not None and not (isinstance(value, list) and len(value) != 1)


def check_currency(catalog: Catalog, requested: list[str], request: MetricRequest) -> None:
    """An amount of money is never served across currencies (SEMANTICS.md): ask which one is meant."""
    if currency_pinned(request):
        return
    money = [name for name in requested if catalog.money(name)]
    if not money:
        return
    raise ClarifyError(
        f"{', '.join(money)}: money is in the currency of each account, invoice or deal, and a total across "
        "currencies has no meaning. Which currency? Filter to one, or add the currency dimension",
        [
            {
                "name": "filters.currency",
                "description": 'One currency: add a filter such as {"currency": "USD"} (ISO 4217)',
            },
            {
                "name": "dimensions.currency",
                "description": 'Every currency apart: add "currency" to dimensions for one row per currency',
            },
        ],
    )
