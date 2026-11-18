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


def test_outside_the_window_only_the_reports_traps_are_disclosed(catalog: Catalog) -> None:
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["meta_spend"], dimensions=["date", "currency"], time_range=OLD),
        _executor(),
        TODAY,
    )
    assert answer.mood is Mood.DISCLOSE
    assert answer.caveats == [
        "meta_ads.campaigns: Meta restates conversions and spend for up to 28 days; recent days change."
    ]


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
    assert set(payload) == {
        "mood",
        "reason",
        "caveats",
        "candidates",
        "comparison",
        "sql",
        "columns",
        "rows",
        "truncated",
    }
    assert payload["mood"] == "clarify" and payload["comparison"] is None


def _missing(table: str) -> Any:
    def execute(sql: str) -> tuple[list[str], list[list[Any]]]:
        raise DatabaseError(
            "Received ClickHouse exception, code: 60, server response: Code: 60. DB::Exception: Unknown "
            f"table expression identifier '{table}' in scope SELECT ad_name, sum(spend) AS meta_ad_spend "
            f"FROM {table} WHERE report = 'ads'. (UNKNOWN_TABLE) (version 25.8.33.6 (official build))"
        )

    return execute


def test_a_report_that_was_never_synced_is_refused_in_plain_words(catalog: Catalog) -> None:
    """Every marts view is created by its pipeline after the first load: one that is not there yet is a
    fact about the warehouse, and the agent is told which report is missing, not shown a ClickHouse error."""
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["meta_ad_spend"], dimensions=["ad_name", "currency"], time_range=OLD),
        _missing("marts.meta_ads_ads"),
        TODAY,
    )
    assert answer.mood is Mood.REFUSE and answer.rows == []
    assert answer.reason == (
        "not loaded in this warehouse: meta_ads.ads (view marts.meta_ads_ads). The source has not synced "
        "this report yet, so there is nothing to answer from"
    )
    cross = answer_request(
        catalog,
        MetricRequest(metrics=["ad_spend"], filters={"currency": "USD"}, time_range=OLD),
        _missing("marts.linkedin_ads_campaigns"),
        TODAY,
    )
    assert cross.reason.startswith(
        "not loaded in this warehouse: linkedin_ads.campaigns (view marts.linkedin_ads_campaigns)."
    )
    assert "DB::Exception" not in answer.reason + cross.reason


def test_a_missing_campaign_key_mapping_says_how_it_is_created(catalog: Catalog) -> None:
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["meta_clicks"], dimensions=["campaign_key"], time_range=OLD),
        _missing("marts.campaign_keys"),
        TODAY,
    )
    assert answer.mood is Mood.REFUSE and "marts.campaign_keys does not exist yet" in answer.reason
    assert "omw.metrics.setup" in answer.reason


def test_a_view_whose_name_only_starts_like_the_reports_is_not_blamed(catalog: Catalog) -> None:
    """marts.meta_ads_campaigns is a prefix of marts.meta_ads_campaigns_old: the error must name the view."""
    answer = answer_request(
        catalog,
        MetricRequest(metrics=["meta_clicks"], time_range=OLD),
        _missing("marts.meta_ads_campaigns_old"),
        TODAY,
    )
    assert answer.mood is Mood.REFUSE and answer.reason.startswith("the warehouse refused the query:")


def test_an_answer_whose_check_verdicts_cannot_be_read_is_disclosed_not_served(catalog: Catalog) -> None:
    """The numbers came back, the verdict query did not (here: the hourly quota ran out between the two).
    Whether the source is stale or a check failed is then unknown, and the answer has to say so. Only a
    warehouse where the checks never ran, and the view does not exist, has nothing to disclose."""

    def execute(sql: str) -> tuple[list[str], list[list[Any]]]:
        if "ops.check_verdicts" in sql:
            raise DatabaseError(
                "Code: 201. DB::Exception: Quota for user `agent` for 3600s has been exceeded: "
                "queries = 1001/1000. (QUOTA_EXCEEDED)"
            )
        return ["date", "sessions"], [[date(2026, 1, 1), 1]]

    request = MetricRequest(metrics=["sessions"], time_range=OLD)
    answer = answer_request(catalog, request, execute, TODAY)
    assert answer.mood is Mood.DISCLOSE and answer.rows == [[date(2026, 1, 1), 1]]
    assert any("check verdicts could not be read" in c and "QUOTA_EXCEEDED" in c for c in answer.caveats)

    never_ran = answer_request(catalog, request, _executor(), TODAY)
    assert never_ran.mood is Mood.SERVE and never_ran.caveats == []
    code_60 = _missing("ops.check_verdicts")

    def no_view(sql: str) -> tuple[list[str], list[list[Any]]]:
        return code_60(sql) if "ops.check_verdicts" in sql else execute(sql)

    assert answer_request(catalog, request, no_view, TODAY).mood is Mood.SERVE
