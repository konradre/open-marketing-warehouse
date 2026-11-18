"""Check the metric catalog against the ``marts`` views in ``sql/`` without a warehouse (run in CI).

The catalog says what each report is (``row_level``, ``snapshot``, its dimensions) and how each metric
aggregates. The views say what is really there: their columns, the comment on each and whether the view
groups. This module reads the ``CREATE VIEW`` statements and lists every place the two disagree:

- a dimension, a campaign-key column or a metric column the view does not have (a filter on it would only
  fail in the warehouse);
- a dated report whose view has no ``date`` or names another report grain; a snapshot whose view has one;
- a report declared ``row_level`` over a view that aggregates;
- a ``sum`` or ``rate`` over a column the view's own comment calls not additive (reach, daily users).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from omw.metrics.schema import CAMPAIGN_KEY, TIME_DIMENSION, Catalog

_VIEW = re.compile(
    r"CREATE OR REPLACE VIEW\s+(?P<name>[\w.]+)\s*\((?P<columns>.*?)\)\s*DEFINER\b(?P<body>.*?);\s*$",
    re.DOTALL | re.MULTILINE,
)
_COLUMN = re.compile(r"^\s*(?P<name>\w+)\s+[^\n]*?COMMENT\s+'(?P<comment>(?:[^'\\]|\\.)*)'", re.MULTILINE)
_STRING = re.compile(r"'(?:[^'\\]|\\.)*'")
_WORD = re.compile(r"\b[A-Za-z_]\w*\b(?!\s*\()")
_KEYWORDS = {"and", "or", "not", "in", "is", "null", "distinct", "as", "true", "false"}
NOT_ADDITIVE = "not additive"


@dataclass(frozen=True)
class View:
    name: str
    columns: dict[str, str]
    """Column -> its comment."""
    body: str

    @property
    def aggregates(self) -> bool:
        """True when the view's own SELECT groups its rows (a GROUP BY inside a joined subquery does not)."""
        depth = 0
        outer: list[str] = []
        for char in _STRING.sub("''", self.body):
            depth += char == "("
            depth -= char == ")"
            outer.append(char if depth == 0 else " ")
        return bool(re.search(r"\bGROUP\s+BY\b", "".join(outer), re.IGNORECASE))


def read_views(sql_dir: Path) -> dict[str, View]:
    """Every view created in ``sql_dir``, by qualified name."""
    views: dict[str, View] = {}
    for path in sorted(sql_dir.glob("*.sql")):
        for match in _VIEW.finditer(path.read_text()):
            columns = {c["name"]: c["comment"] for c in _COLUMN.finditer(match["columns"])}
            views[match["name"]] = View(match["name"], columns, match["body"])
    return views


def columns_of(expression: str) -> set[str]:
    """The column names an SQL expression reads: every bare word that is not a function, a keyword or
    inside a string."""
    words = _WORD.findall(_STRING.sub("''", expression))
    return {word for word in words if word.lower() not in _KEYWORDS}


def check_views(catalog: Catalog, sql_dir: Path) -> list[str]:
    """Every disagreement between the catalog and the views, in plain words. Empty when they agree."""
    views = read_views(sql_dir)
    problems: list[str] = []
    for report in catalog.reports.values():
        view = views.get(report.view)
        if view is None:
            problems.append(f"{report.key}: no view {report.view} in {sql_dir.name}/")
            continue
        has = set(view.columns)
        needed = set(report.dimensions) | columns_of(report.campaign_key or "")
        for column in sorted(needed - has):
            problems.append(f"{report.key}: {report.view} has no column {column!r}")
        if report.snapshot:
            if TIME_DIMENSION in has:
                problems.append(f"{report.key}: declared snapshot but {report.view} has a date column")
        else:
            if TIME_DIMENSION not in has:
                problems.append(f"{report.key}: {report.view} has no date column; declare it snapshot")
            if f"always {report.name}:" not in view.columns.get("report", ""):
                problems.append(
                    f"{report.key}: {report.view} does not carry the report grain {report.name!r}"
                )
        if report.row_level and view.aggregates:
            problems.append(f"{report.key}: declared row_level but {report.view} groups its rows")
    for metric in catalog.metrics.values():
        view = views.get(catalog.report_of(metric).view)
        if view is None:
            continue
        for column in sorted(columns_of(metric.expr)):
            if column not in view.columns:
                problems.append(f"{metric.name}: {view.name} has no column {column!r}")
            elif metric.aggregation in ("sum", "rate") and NOT_ADDITIVE in view.columns[column].lower():
                problems.append(
                    f"{metric.name}: {column} is not additive ({view.name} says so); declare aggregation "
                    "native, not " + metric.aggregation
                )
    if CAMPAIGN_KEY in {name for report in catalog.reports.values() for name in report.dimensions}:
        problems.append(f"{CAMPAIGN_KEY} is declared through a report's campaign_key, not as a dimension")
    return problems
