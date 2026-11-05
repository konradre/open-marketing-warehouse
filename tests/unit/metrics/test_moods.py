"""Every mood, end to end through the runner with a fake executor."""

from __future__ import annotations

from datetime import date
from typing import Any

from clickhouse_connect.driver.exceptions import DatabaseError

from omw.metrics import Catalog, MetricRequest, Mood, TimeRange
from omw.metrics.runner import answer_request
from tests.unit.metrics.conftest import OCTOBER, OLD, TODAY

VERDICT_COLUMNS = ["source", "check", "report", "status", "detail"]


def _executor(
    rows: list[list[Any]] | None = None,
    columns: list[str] | None = None,
    verdicts: list[list[Any]] | None = None,
) -> Any:
    """A fake warehouse: the metric query returns ``rows``; the verdict query returns ``verdicts``
    (``None`` models a warehouse where the checks never ran and the view does not exist)."""
    calls: list[str] = []

    def execute(sql: str) -> tuple[list[str], list[list[Any]]]:
        calls.append(sql)
        if "ops.check_verdicts" in sql:
            if verdicts is None:
                raise DatabaseError("Table ops.check_verdicts does not exist")
            return VERDICT_COLUMNS, verdicts
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
    assert execute.calls[1] == (
        "SELECT source, check, report, status, detail FROM ops.check_verdicts "
        "WHERE source IN ('ga4') AND status != 'pass' ORDER BY source, check, report"
    )


def test_disclose_a_stale_source_and_a_failed_check_on_the_report_used(catalog: Catalog) -> None:
    verdicts = [
        ["ga4", "freshness", "sync", "fail", "last sync 40.0h ago, budget 30h"],
        ["ga4", "overcount", "channels", "fail", "2 of 9 groups exceed core by more than 1% on sessions"],
        ["ga4", "rowcount", "landing_pages", "warn", "no rows in the current window"],
    ]
    core = answer_request(
        catalog,
        MetricRequest(metrics=["sessions"], dimensions=["date"], time_range=OLD),
        _executor(verdicts=verdicts),
        TODAY,
    )
    assert core.mood is Mood.DISCLOSE
    assert core.caveats == [
        "ga4 freshness check fail: last sync 40.0h ago, budget 30h; its numbers may be stale"
    ]
    channels = answer_request(
        catalog,
        MetricRequest(metrics=["channel_sessions"], dimensions=["channel_group"], time_range=OLD),
        _executor(verdicts=verdicts),
        TODAY,
    )
    assert "ga4.channels overcount check fail: 2 of 9 groups exceed core by more than 1% on sessions" in (
        channels.caveats
    )
    assert not any("landing_pages" in c for c in channels.caveats)
    clean = answer_request(
        catalog,
        MetricRequest(metrics=["sessions"], dimensions=["date"], time_range=OLD),
        _executor(verdicts=[]),
        TODAY,
    )
    assert clean.mood is Mood.SERVE


def test_cross_source_verdicts_cover_every_source_in_the_plan(catalog: Catalog) -> None:
    execute = _executor(verdicts=[["twenty", "freshness", "sync", "fail", "never synced: no sync log"]])
    answer = answer_request(
        catalog,
        MetricRequest(
            metrics=["pipeline_cac"], dimensions=["date"], filters={"currency": "EUR"}, time_range=OLD
        ),
        execute,
        TODAY,
    )
    assert "source IN ('google_ads', 'linkedin_ads', 'meta_ads', 'twenty')" in execute.calls[1]
    assert any(c.startswith("twenty freshness check fail: never synced") for c in answer.caveats)


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
    assert [c["name"] for c in answer.candidates] == ["google_spend", "linkedin_spend", "meta_spend"]
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
