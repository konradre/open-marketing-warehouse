"""Every mood, end to end through the runner with a fake executor."""

from __future__ import annotations

from datetime import date
from typing import Any

from clickhouse_connect.driver.exceptions import DatabaseError

from omw.metrics import Catalog, MetricRequest, Mood, TimeRange
from omw.metrics.runner import answer_request
from tests.unit.metrics.conftest import OCTOBER, OLD, TODAY


def _executor(rows: list[list[Any]] | None = None, columns: list[str] | None = None) -> Any:
    calls: list[str] = []

    def execute(sql: str) -> tuple[list[str], list[list[Any]]]:
        calls.append(sql)
        return columns or ["date", "value"], rows if rows is not None else [[date(2026, 1, 1), 1]]

    execute.calls = calls  # type: ignore[attr-defined]
    return execute


def test_serve_when_nothing_is_wrong(catalog: Catalog) -> None:
    execute = _executor()
    answer = answer_request(
        catalog, MetricRequest(metrics=["sessions"], dimensions=["date"], time_range=OLD), execute, TODAY
    )
    assert answer.mood is Mood.SERVE and answer.caveats == [] and answer.reason == ""
    assert answer.columns == ["date", "value"] and answer.rows == [[date(2026, 1, 1), 1]]
    assert execute.calls[0] == answer.sql and answer.sql.startswith("SELECT date, sum(sessions) AS sessions")


def test_disclose_inside_metas_restatement_window(catalog: Catalog) -> None:
    answer = answer_request(
        catalog,
        MetricRequest(
            metrics=["meta_spend"], dimensions=["date"], filters={"currency": "USD"}, time_range=OCTOBER
        ),
        _executor(),
        TODAY,
    )
    assert answer.mood is Mood.DISCLOSE
    assert any("28-day restatement window" in c for c in answer.caveats)
    assert any("restates" in c for c in answer.caveats)
    assert not any("mixes currencies" in c for c in answer.caveats)


def test_disclose_outside_the_window_only_for_the_currency_and_traps(catalog: Catalog) -> None:
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["meta_spend"], dimensions=["date"], time_range=OLD),
        _executor(),
        TODAY,
    )
    assert answer.mood is Mood.DISCLOSE
    assert not any("restatement window" in c for c in answer.caveats)
    assert any("mixes currencies" in c for c in answer.caveats)


def test_disclose_anonymised_queries_and_ga4_other(catalog: Catalog) -> None:
    queries = answer_request(
        catalog,
        MetricRequest(metrics=["query_clicks"], dimensions=["date", "query"], time_range=OLD),
        _executor(),
        TODAY,
    )
    assert queries.mood is Mood.DISCLOSE and any("anonymised" in c for c in queries.caveats)
    channels = answer_request(
        catalog,
        MetricRequest(metrics=["channel_sessions"], dimensions=["channel_group"], time_range=OLD),
        _executor(),
        TODAY,
    )
    assert channels.mood is Mood.DISCLOSE and any("(other)" in c for c in channels.caveats)


def test_disclose_truncated_result(catalog: Catalog) -> None:
    rows = [[date(2026, 1, 1), i] for i in range(5)]
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["sessions"], dimensions=["date"], time_range=OLD, limit=5),
        _executor(rows),
        TODAY,
    )
    assert answer.mood is Mood.DISCLOSE and answer.truncated and "limit of 5 rows" in answer.caveats[-1]
    assert "LIMIT 5" in answer.sql


def test_clarify_on_an_ambiguous_name(catalog: Catalog) -> None:
    execute = _executor()
    answer = answer_request(catalog, MetricRequest(metrics=["spend"], time_range=OLD), execute, TODAY)
    assert answer.mood is Mood.CLARIFY
    assert [c["name"] for c in answer.candidates] == ["google_spend", "meta_spend"]
    assert all(c["description"] for c in answer.candidates)
    assert execute.calls == [] and answer.sql == ""


def test_refuse_a_metric_at_a_grain_it_lacks(catalog: Catalog) -> None:
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["search_clicks"], dimensions=["page"], time_range=OLD),
        _executor(),
        TODAY,
    )
    assert answer.mood is Mood.REFUSE and "has no dimension 'page'" in answer.reason


def test_refuse_a_non_additive_measure_summed(catalog: Catalog) -> None:
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["meta_reach"], dimensions=["date"], time_range=OLD),
        _executor(),
        TODAY,
    )
    assert answer.mood is Mood.REFUSE and "not additive" in answer.reason
    at_grain = answer_request(
        catalog,
        MetricRequest(
            metrics=["meta_reach"],
            dimensions=["date", "account_id", "currency", "campaign_id", "campaign_name", "campaign_key"],
            time_range=OLD,
        ),
        _executor(),
        TODAY,
    )
    assert at_grain.mood is not Mood.REFUSE


def test_refuse_a_dimension_on_one_side_of_a_cross_source_join(catalog: Catalog) -> None:
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["roas"], dimensions=["date", "campaign_name"], time_range=OLD),
        _executor(),
        TODAY,
    )
    assert answer.mood is Mood.REFUSE and "cross-source measure" in answer.reason
    filtered = answer_request(
        catalog,
        MetricRequest(
            metrics=["meta_spend", "google_spend"], filters={"channel_type": "SEARCH"}, time_range=OLD
        ),
        _executor(),
        TODAY,
    )
    assert filtered.mood is Mood.REFUSE and "only one side" in filtered.reason


def test_refuse_when_the_warehouse_refuses(catalog: Catalog) -> None:
    def execute(sql: str) -> tuple[list[str], list[list[Any]]]:
        raise DatabaseError("Code: 497. DB::Exception: agent: Not enough privileges")

    answer = answer_request(catalog, MetricRequest(metrics=["sessions"], time_range=OLD), execute, TODAY)
    assert answer.mood is Mood.REFUSE and "Not enough privileges" in answer.reason


def test_answers_serialise_with_structured_fields(catalog: Catalog) -> None:
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["spend"], time_range=TimeRange(start=OLD.start, end=OLD.end)),
        _executor(),
        TODAY,
    )
    payload = answer.to_dict()
    assert set(payload) == {"mood", "reason", "caveats", "candidates", "sql", "columns", "rows", "truncated"}
    assert payload["mood"] == "clarify"
