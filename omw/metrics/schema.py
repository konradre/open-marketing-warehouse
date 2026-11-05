"""Metric definitions: the pydantic schema for ``metrics/*.yaml`` and the catalog that validates them.

A ``Report`` is one ``marts`` view (a report grain, DESIGN.md §4) with its dimensions and the expression
that derives the cross-source ``campaign_key``. A ``Metric`` is one measure on one report: an aggregate
expression, a unit, the grains it is valid at and how it aggregates (``sum`` freely, ``rate`` recomputed
at query time from additive parts, ``native`` only at its own grain). A ``DerivedMetric`` combines metrics
by name, possibly across sources, and is only valid at its common grain.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

Aggregation = Literal["sum", "rate", "native"]
Unit = Literal["count", "currency", "ratio", "percent", "position", "events"]

TIME_DIMENSION = "date"
CAMPAIGN_KEY = "campaign_key"
PERIOD = "period"
"""A reserved output column for ``compare`` requests: ``current`` or ``previous``."""

METRICS_DIR = Path(__file__).resolve().parents[2] / "metrics"
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class Report(BaseModel):
    source: str
    name: str
    view: str
    description: str = ""
    dimensions: dict[str, str] = Field(default_factory=dict)
    """Dimension column -> plain-words description. ``date`` is implicit on every report."""
    campaign_key: str | None = None
    """SQL expression deriving the cross-source campaign key from the report's own columns."""
    restatement_days: int = 0
    """Days back from today the source still revises; answers inside it are disclosed."""
    traps: list[str] = Field(default_factory=list)
    """Caveats that travel with every answer from this report (anonymised queries, (other) rows, ...)."""

    @field_validator("name", "source")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError(f"not a valid identifier: {value!r}")
        return value

    @property
    def key(self) -> str:
        return f"{self.source}.{self.name}"

    @property
    def all_dimensions(self) -> list[str]:
        extra = [CAMPAIGN_KEY] if self.campaign_key else []
        return [TIME_DIMENSION, *self.dimensions, *extra]


class Metric(BaseModel):
    name: str
    source: str
    report: str
    expr: str
    """ClickHouse aggregate expression over the report's columns."""
    unit: Unit
    aggregation: Aggregation = "sum"
    grains: list[str] = Field(default_factory=list)
    """Dimensions the metric may be grouped by; ``date`` is always allowed."""
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    approximate: bool = False
    """True for sketch-based counts (uniq): disclosed as approximate."""

    @field_validator("name", "source", "report")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError(f"not a valid identifier: {value!r}")
        return value

    @property
    def additive(self) -> bool:
        return self.aggregation == "sum"

    @property
    def report_key(self) -> str:
        return f"{self.source}.{self.report}"


class DerivedMetric(BaseModel):
    name: str
    derived: str
    """Arithmetic over metric names, for example ``revenue / ad_spend``."""
    unit: Unit
    requires_common_grain: list[str] = Field(default_factory=list)
    description: str = ""
    aliases: list[str] = Field(default_factory=list)

    @property
    def operands(self) -> list[str]:
        return sorted({token for token in re.findall(r"[a-z][a-z0-9_]*", self.derived)})


class MetricsFile(BaseModel):
    """One ``metrics/*.yaml`` file."""

    source: str | None = None
    reports: list[Report] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    derived: list[DerivedMetric] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _fill_source(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("source"):
            for item in data.get("reports", []) + data.get("metrics", []):
                item.setdefault("source", data["source"])
        return data


class Catalog(BaseModel):
    reports: dict[str, Report]
    """Keyed by ``source.report``."""
    metrics: dict[str, Metric]
    derived: dict[str, DerivedMetric]

    @model_validator(mode="after")
    def _references(self) -> Catalog:
        for metric in self.metrics.values():
            report = self.reports.get(metric.report_key)
            if report is None:
                raise ValueError(f"metric {metric.name}: unknown report {metric.report_key}")
            unknown = set(metric.grains) - set(report.all_dimensions)
            if unknown:
                raise ValueError(
                    f"metric {metric.name}: grains {sorted(unknown)} are not dimensions of {report.key}"
                )
        for derived in self.derived.values():
            for operand in derived.operands:
                if operand not in self.metrics and operand not in self.derived:
                    raise ValueError(f"derived {derived.name}: unknown operand {operand}")
                if operand == derived.name:
                    raise ValueError(f"derived {derived.name} refers to itself")
            for base in self.base_metrics(derived.name):
                missing = set(derived.requires_common_grain) - set(base.grains) - {TIME_DIMENSION}
                if missing:
                    raise ValueError(
                        f"derived {derived.name}: {base.name} lacks the common grain {sorted(missing)}"
                    )
        names = [*self.metrics, *self.derived]
        if len(set(names)) != len(names):
            raise ValueError("metric and derived names must be unique")
        return self

    def base_metrics(self, name: str, _seen: frozenset[str] = frozenset()) -> list[Metric]:
        """The base metrics a (derived) metric depends on."""
        if name in self.metrics:
            return [self.metrics[name]]
        derived = self.derived[name]
        if name in _seen:
            raise ValueError(f"derived {name} is circular")
        found: list[Metric] = []
        for operand in derived.operands:
            for base in self.base_metrics(operand, _seen | {name}):
                if base not in found:
                    found.append(base)
        return found

    def resolve(self, name: str) -> list[str]:
        """Metric or derived names a requested name may mean: an exact name, else every alias match."""
        if name in self.metrics or name in self.derived:
            return [name]
        matches = [m.name for m in self.metrics.values() if name in m.aliases]
        matches += [d.name for d in self.derived.values() if name in d.aliases]
        return matches

    def report_of(self, metric: Metric) -> Report:
        return self.reports[metric.report_key]

    def dimension_descriptions(self) -> dict[str, dict[str, str]]:
        """Per report key, its dimensions and descriptions (``date`` and ``campaign_key`` included)."""
        out: dict[str, dict[str, str]] = {}
        for key, report in self.reports.items():
            dims = {TIME_DIMENSION: "Day of the report (the report's own time zone)", **report.dimensions}
            if report.campaign_key:
                dims[CAMPAIGN_KEY] = (
                    "Cross-source campaign key (marts.campaign_keys, else the source default)"
                )
            out[key] = dims
        return out


def load_catalog(directory: Path = METRICS_DIR) -> Catalog:
    reports: dict[str, Report] = {}
    metrics: dict[str, Metric] = {}
    derived: dict[str, DerivedMetric] = {}
    for path in sorted(directory.glob("*.yaml")):
        loaded = MetricsFile.model_validate(yaml.safe_load(path.read_text()) or {})
        for report in loaded.reports:
            if report.key in reports:
                raise ValueError(f"{path.name}: duplicate report {report.key}")
            reports[report.key] = report
        for metric in loaded.metrics:
            if metric.name in metrics:
                raise ValueError(f"{path.name}: duplicate metric {metric.name}")
            metrics[metric.name] = metric
        for item in loaded.derived:
            if item.name in derived:
                raise ValueError(f"{path.name}: duplicate derived metric {item.name}")
            derived[item.name] = item
    return Catalog(reports=reports, metrics=metrics, derived=derived)
