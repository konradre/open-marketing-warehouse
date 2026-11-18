"""metrics/*.yaml validates against the schema; broken definitions are rejected."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from omw.metrics import Catalog
from omw.metrics.schema import METRICS_DIR, DerivedMetric, Metric, Report

SEMANTICS = Path(__file__).resolve().parents[3] / "SEMANTICS.md"


def test_catalog_loads_every_source(catalog: Catalog) -> None:
    assert {r.source for r in catalog.reports.values()} == {
        "meta_ads",
        "google_ads",
        "search_console",
        "ga4",
        "stripe",
        "linkedin_ads",
        "twenty",
    }
    assert {
        "meta_spend",
        "google_spend",
        "linkedin_spend",
        "revenue",
        "search_clicks",
        "sessions",
        "opportunities",
    } <= set(catalog.metrics)
    assert set(catalog.derived) == {"ad_spend", "roas", "cac", "pipeline_cac", "blended_cpc", "cost_per_lead"}
    for metric in catalog.metrics.values():
        assert metric.description and metric.report_key in catalog.reports
    for derived in catalog.derived.values():
        assert derived.description


def test_every_mart_view_from_the_sources_is_a_report(catalog: Catalog) -> None:
    views = {r.view for r in catalog.reports.values()}
    assert {
        "marts.meta_ads_campaigns",
        "marts.google_ads_campaigns",
        "marts.stripe_revenue",
        "marts.ga4_core",
        "marts.linkedin_ads_campaigns",
        "marts.twenty_pipeline",
    } <= views


def test_semantics_names_every_metric_and_stays_small(catalog: Catalog) -> None:
    text = SEMANTICS.read_text()
    assert len(text.encode()) < 8_000  # the agent's whole reference for ten sources
    for name in list(catalog.metrics) + list(catalog.derived):
        assert name in text, name
    for word in ("serve", "disclose", "clarify", "refuse", "(other)", "28 days", "30 days"):
        assert word in text


def test_aliases_resolve_and_ambiguity_is_visible(catalog: Catalog) -> None:
    assert catalog.resolve("meta_spend") == ["meta_spend"]
    assert catalog.resolve("spend") == ["google_spend", "linkedin_spend", "meta_spend"]
    assert catalog.resolve("nope") == []
    assert [m.name for m in catalog.base_metrics("roas")] == [
        "google_spend",
        "linkedin_spend",
        "meta_spend",
        "revenue",
    ]
    assert [m.name for m in catalog.base_metrics("pipeline_cac")] == [
        "google_spend",
        "linkedin_spend",
        "meta_spend",
        "opportunities",
    ]


def _catalog(**parts: list[dict[str, Any]]) -> Catalog:
    reports = {f"{r['source']}.{r['name']}": Report.model_validate(r) for r in parts.get("reports", [])}
    metrics = {str(m["name"]): Metric.model_validate(m) for m in parts.get("metrics", [])}
    derived = {str(d["name"]): DerivedMetric.model_validate(d) for d in parts.get("derived", [])}
    return Catalog(reports=reports, metrics=metrics, derived=derived)


REPORT: dict[str, Any] = {"source": "x", "name": "r", "view": "marts.x_r", "dimensions": {"a": "A"}}


def test_schema_rejects_unknown_report_grain_and_operand() -> None:
    with pytest.raises(ValueError, match="unknown report"):
        _catalog(
            reports=[REPORT],
            metrics=[{"name": "m", "source": "x", "report": "zz", "expr": "sum(v)", "unit": "count"}],
        )
    with pytest.raises(ValueError, match="not dimensions"):
        _catalog(
            reports=[REPORT],
            metrics=[
                {
                    "name": "m",
                    "source": "x",
                    "report": "r",
                    "expr": "sum(v)",
                    "unit": "count",
                    "grains": ["b"],
                }
            ],
        )
    with pytest.raises(ValueError, match="unknown operand"):
        _catalog(reports=[REPORT], derived=[{"name": "d", "derived": "m / q", "unit": "ratio"}])
    with pytest.raises(ValueError, match="lacks the common grain"):
        _catalog(
            reports=[REPORT],
            metrics=[{"name": "m", "source": "x", "report": "r", "expr": "sum(v)", "unit": "count"}],
            derived=[{"name": "d", "derived": "m / 2", "unit": "ratio", "requires_common_grain": ["a"]}],
        )
    with pytest.raises(ValueError, match="not a valid identifier"):
        Metric.model_validate(
            {"name": "Bad Name", "source": "x", "report": "r", "expr": "1", "unit": "count"}
        )


def test_yaml_files_are_well_formed() -> None:
    for path in METRICS_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text())
        assert isinstance(data, dict), path.name
        assert set(data) <= {"source", "reports", "metrics", "derived"}, path.name


ROWS: dict[str, Any] = {**REPORT, "row_level": True, "dimensions": {"a": "A", "currency": "Currency"}}
COUNT: dict[str, Any] = {"name": "m", "source": "x", "report": "r", "unit": "count"}


@pytest.mark.parametrize(
    ("report", "metric", "reason"),
    [
        # A distinct count is only exact above its grain over rows that were never aggregated.
        (REPORT, {**COUNT, "expr": "uniqExact(id)", "aggregation": "distinct"}, "needs a row_level report"),
        # ... and must be a distinct count: a sum does not become exact by being declared so.
        (ROWS, {**COUNT, "expr": "sum(users)", "aggregation": "distinct"}, "needs a distinct count"),
        # A distinct count declared as anything else would be summed or refused by the wrong rule.
        (ROWS, {**COUNT, "expr": "uniqExact(id)"}, "declare aggregation distinct"),
        (ROWS, {**COUNT, "expr": "uniqExactIf(id, a = 'x')", "aggregation": "native"}, "declare aggregation"),
        (ROWS, {**COUNT, "expr": "count(DISTINCT id)", "aggregation": "sum"}, "declare aggregation distinct"),
        # A sketch is approximate and must say so; an exact count must not claim to be one.
        (ROWS, {**COUNT, "expr": "uniq(id)", "aggregation": "distinct"}, "approximate must be true"),
        (
            ROWS,
            {**COUNT, "expr": "uniqExact(id)", "aggregation": "distinct", "approximate": True},
            "approximate must be true for a sketch",
        ),
        # Money needs the currency dimension, on the report and in the metric's grains.
        (REPORT, {**COUNT, "expr": "sum(v)", "unit": "currency"}, "needs the 'currency' dimension"),
        (ROWS, {**COUNT, "expr": "sum(v)", "unit": "currency", "grains": ["a"]}, "needs the 'currency'"),
    ],
)
def test_schema_rejects_an_aggregation_the_report_or_expression_cannot_carry(
    report: dict[str, Any], metric: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        _catalog(reports=[report], metrics=[metric])


def test_schema_accepts_the_declared_forms() -> None:
    catalog = _catalog(
        reports=[ROWS],
        metrics=[
            {**COUNT, "expr": "uniqExact(id)", "aggregation": "distinct", "grains": ["a"]},
            {**COUNT, "name": "n", "expr": "uniq(id)", "aggregation": "distinct", "approximate": True},
            {**COUNT, "name": "v", "expr": "sum(v)", "unit": "currency", "grains": ["a", "currency"]},
        ],
    )
    assert catalog.metrics["m"].aggregation == "distinct" and catalog.money("v") and not catalog.money("m")


def test_schema_requires_the_currency_in_the_common_grain_of_a_derived_money_measure() -> None:
    money = {**COUNT, "expr": "sum(v)", "unit": "currency", "grains": ["a", "currency"]}
    with pytest.raises(ValueError, match="common grain must include 'currency'"):
        _catalog(
            reports=[ROWS],
            metrics=[money],
            derived=[{"name": "d", "derived": "m / 2", "unit": "ratio", "requires_common_grain": ["a"]}],
        )


def test_a_snapshot_report_has_no_date_dimension() -> None:
    snapshot = {**ROWS, "snapshot": True}
    assert "date" not in Report.model_validate(snapshot).all_dimensions
    assert "date" in Report.model_validate(ROWS).all_dimensions
    with pytest.raises(ValueError, match="lacks the common grain"):
        _catalog(
            reports=[snapshot],
            metrics=[{**COUNT, "expr": "count()", "grains": ["a"]}],
            derived=[{"name": "d", "derived": "m / 2", "unit": "ratio", "requires_common_grain": ["date"]}],
        )
