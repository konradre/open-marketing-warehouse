"""Twenty: shapes, cursor paging, the updatedAt filter across runs, auth, errors, pipeline on dummy."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import dlt
import pytest
import responses

from omw.metrics import load_catalog
from omw.pipelines import twenty as twenty_pipeline
from omw.pipelines.marts import SQL_DIR, statements
from omw.pipelines.twenty import DEFAULT_WON_STAGES, MARTS_SQL, render_won_stages, run_daily, won_stages
from omw.sources.twenty import ATTRIBUTION_FIELDS, shape_company, shape_opportunity, shape_person, twenty
from omw.sources.twenty.source import default_session, updated_at_filter
from tests.unit.twenty.conftest import BASE_URL, REST, body, fixture, register_api


def _session() -> Any:
    return default_session(max_attempts=3, backoff_factor=0.0)


def test_shapes_flatten_composites_micros_and_attribution() -> None:
    company = shape_company(fixture("companies_page_1")["data"]["companies"][0])
    assert company["domain"] == "acme.example" and company["annual_recurring_revenue"] == Decimal("48000")
    assert company["currency"] == "EUR" and company["ideal_customer_profile"] is True
    assert (
        company["updated_at"] == datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
        and company["deleted_at"].year == 1970
    )
    assert company["campaign_key"] == "meta:23850000000000001" and company["utm_medium"] == "paid_social"
    plain = shape_company(fixture("companies_page_1")["data"]["companies"][1])
    assert plain["annual_recurring_revenue"] == Decimal(0) and all(
        plain[c] == "" for c in ATTRIBUTION_FIELDS.values()
    )
    person = shape_person(fixture("people")["data"]["people"][0])
    assert (
        person["first_name"] == "Ada"
        and person["email"] == "ada@acme.example"
        and person["company_id"].endswith("0001")
    )
    opp = shape_opportunity(fixture("opportunities")["data"]["opportunities"][0])
    assert opp["stage"] == "PROPOSAL" and opp["amount"] == Decimal("36000") and opp["currency"] == "EUR"
    assert opp["person_id"].endswith("0001") and opp["close_date"] == datetime(2026, 11, 15, tzinfo=UTC)
    no_contact = shape_opportunity(fixture("opportunities")["data"]["opportunities"][2])
    assert no_contact["person_id"] == "" and no_contact["campaign_key"] == "events:webinar_oct"


def test_updated_at_filter_syntax() -> None:
    assert updated_at_filter("2026-10-01T10:00:00.000Z") == 'updatedAt[gte]:"2026-10-01T10:00:00.000Z"'
    assert (
        updated_at_filter(datetime(2026, 10, 1, 10, 0, tzinfo=UTC)) == 'updatedAt[gte]:"2026-10-01T10:00:00Z"'
    )


@responses.activate
def test_resources_page_with_the_cursor_and_carry_the_key() -> None:
    register_api(responses)
    source = twenty(base_url=BASE_URL, api_key="twenty-key", session=_session())
    assert set(source.resources) == {
        "companies",
        "people",
        "opportunities",
        "notes",
        "tasks",
        "note_targets",
        "task_targets",
        "deleted_companies",
        "deleted_people",
        "deleted_opportunities",
        "deleted_notes",
        "deleted_tasks",
        "deleted_note_targets",
        "deleted_task_targets",
        "sync_windows",
    }
    companies = list(source.resources["companies"])
    assert [c["name"] for c in companies] == ["Acme Widgets", "Globex", "Initech"]
    assert len(list(source.resources["opportunities"])) == 3
    requests_made = [c.request for c in responses.calls]
    assert all(r.headers["Authorization"] == "Bearer twenty-key" for r in requests_made)
    first = parse_qs(urlparse(str(requests_made[0].url)).query)
    assert first["limit"] == ["60"] and first["order_by"] == ["updatedAt[AscNullsLast]"]
    assert first["filter"] == ['updatedAt[gte]:"1970-01-01T00:00:00Z"']
    hints: dict[str, Any] = dict(source.resources["opportunities"].compute_table_schema())
    assert hints["write_disposition"] == "merge" and [
        c for c, h in hints["columns"].items() if h.get("primary_key")
    ] == ["id"]
    assert hints["x-clickhouse-sort"] == ["id"] and all(
        h["nullable"] is False for h in hints["columns"].values()
    )


@responses.activate
def test_unauthorized_surfaces_and_is_not_retried() -> None:
    responses.get(
        f"{REST}companies", body=body("error_unauthorized"), status=401, content_type="application/json"
    )
    with pytest.raises(Exception, match="401"):
        list(twenty(base_url=BASE_URL, api_key="bad", session=_session()).resources["companies"])
    assert len(responses.calls) == 1


def test_validation() -> None:
    with pytest.raises(ValueError, match="base_url"):
        twenty(base_url="", api_key="k")
    with pytest.raises(ValueError, match="api_key"):
        twenty(base_url=BASE_URL, api_key=None)


def _pipeline(tmp_path: Path) -> dlt.Pipeline:
    return dlt.pipeline(
        pipeline_name="twenty_unit",
        destination=dlt.destinations.dummy(completed_prob=1.0),
        dataset_name="twenty",
        pipelines_dir=str(tmp_path),
    )


@responses.activate
def test_second_run_asks_only_for_records_updated_since_the_last_one(tmp_path: Path) -> None:
    register_api(responses)
    pipeline = _pipeline(tmp_path)
    counts = run_daily(pipeline, base_url=BASE_URL, api_key="k", session=_session(), marts_sql=None)
    assert (
        counts["companies"] == 3
        and counts["people"] == 2
        and counts["opportunities"] == 3
        and counts["sync_windows"] == 1
    )
    responses.reset()
    register_api(responses, opportunities="opportunities_updated")
    counts = run_daily(pipeline, base_url=BASE_URL, api_key="k", session=_session(), marts_sql=None)
    opportunity_calls = [c.request for c in responses.calls if "/rest/opportunities" in str(c.request.url)]
    query = parse_qs(urlparse(str(opportunity_calls[0].url)).query)
    assert query["filter"] == [
        'updatedAt[gte]:"2026-10-03T08:00:00Z"'
    ]  # the newest updatedAt of the first run
    assert counts.get("opportunities", 0) == 1
    company_calls = [c.request for c in responses.calls if "/rest/companies" in str(c.request.url)]
    assert (
        'updatedAt[gte]:"2026-10-03T16:45:00Z"'
        in parse_qs(urlparse(str(company_calls[0].url)).query)["filter"][0]
    )


def test_marts_sql_views() -> None:
    stmts = statements((SQL_DIR / "90_twenty_marts.sql").read_text())
    assert len(stmts) == 3 and all(
        "DEFINER = CURRENT_USER SQL SECURITY DEFINER AS" in s and ";" not in s for s in stmts
    )
    assert (
        "multiIf(o.campaign_key != '', o.campaign_key, p.campaign_key != '', p.campaign_key, c.campaign_key)"
        in stmts[0]
    )
    assert "GROUP BY source, stage, currency, campaign_key" in stmts[1]


@responses.activate
def test_deleted_pass_asks_for_soft_deleted_records_into_the_same_table() -> None:
    register_api(responses, deleted_opportunities="opportunities_deleted")
    source = twenty(base_url=BASE_URL, api_key="twenty-key", session=_session())
    deleted = source.resources["deleted_opportunities"]
    assert deleted.table_name == "opportunities"
    rows = list(deleted)
    assert [r["name"] for r in rows] == ["Globex - pilot"]
    assert rows[0]["deleted_at"].year == 2026 and rows[0]["deleted_at"].month == 10
    query = parse_qs(urlparse(str(responses.calls[0].request.url)).query)
    assert query["filter"] == ['and(deletedAt[is]:NOT_NULL,deletedAt[gte]:"1970-01-01T00:00:00Z")']
    assert query["order_by"] == ["deletedAt[AscNullsLast]"] and query["limit"] == ["60"]
    assert list(source.resources["deleted_companies"]) == []
    hints: dict[str, Any] = dict(deleted.compute_table_schema())
    assert hints["write_disposition"] == "merge" and hints["name"] == "opportunities"


@responses.activate
def test_notes_tasks_and_targets_flatten_bodies_and_owners(tmp_path: Path) -> None:
    register_api(responses)
    # A fresh pipeline context: the incremental cursors of the pipeline tests above must not apply here.
    dlt.pipeline(
        "twenty_unit_activity", destination=dlt.destinations.dummy(), pipelines_dir=str(tmp_path)
    ).activate()
    source = twenty(base_url=BASE_URL, api_key="twenty-key", session=_session())
    notes = list(source.resources["notes"])
    assert [n["title"] for n in notes] == ["Kick-off call", "Pricing questions"]
    assert notes[0]["body"] == "Agreed on a 120-seat pilot." and notes[0]["created_by"] == "Dana Rep"
    tasks = list(source.resources["tasks"])
    assert tasks[0]["status"] == "DONE" and tasks[0]["due_at"].day == 3 and tasks[1]["status"] == "TODO"
    note_targets = list(source.resources["note_targets"])
    assert note_targets[0]["note_id"] == notes[0]["id"] and note_targets[0]["opportunity_id"].endswith("0001")
    assert note_targets[2]["person_id"].endswith("0002") and note_targets[2]["company_id"] == ""
    task_targets = list(source.resources["task_targets"])
    assert [t["task_id"] for t in task_targets] == [tasks[0]["id"], tasks[1]["id"]]
    query = parse_qs(urlparse(str(responses.calls[-1].request.url)).query)
    assert "/rest/taskTargets" in str(responses.calls[-1].request.url) and query["limit"] == ["60"]
    hints: dict[str, Any] = dict(source.resources["note_targets"].compute_table_schema())
    assert hints["x-clickhouse-sort"] == ["id"] and "note_id" in hints["columns"]
    stmts = statements((SQL_DIR / "90_twenty_marts.sql").read_text())
    assert "CREATE OR REPLACE VIEW marts.twenty_activity" in stmts[2] and "UNION ALL" in stmts[2]


def test_the_closed_won_stages_are_checked_before_they_reach_a_view() -> None:
    assert won_stages(None) == DEFAULT_WON_STAGES == ("CUSTOMER",)
    assert won_stages(["WON", " RENEWED ", "WON"]) == ("WON", "RENEWED")
    assert won_stages("CLOSED_WON") == ("CLOSED_WON",)  # a bare string in the config is one stage
    assert won_stages(["Closed - Won EU"]) == ("Closed - Won EU",)
    with pytest.raises(ValueError, match="at least one stage"):
        won_stages([])
    for bad in ("", "WON') OR 1 = 1 --", "a'b", "WON\nX", "*/"):
        with pytest.raises(ValueError, match="is not a stage name"):
            won_stages([bad])


def test_the_marts_file_is_its_own_default_and_takes_the_configured_stages() -> None:
    text = MARTS_SQL.read_text()
    assert render_won_stages(DEFAULT_WON_STAGES)(text) == text  # the committed file is the default, as is
    assert "o.stage IN (/*won_stages*/'CUSTOMER'/*won_stages*/) AS is_won" in text
    rendered = render_won_stages(["WON", "RENEWED"])(text)
    stmts = statements(rendered)
    assert (
        len(stmts) == 3 and "o.stage IN (/*won_stages*/'WON', 'RENEWED'/*won_stages*/) AS is_won" in stmts[0]
    )
    assert "Closed-won stages here: WON, RENEWED'" in stmts[0] and "'CUSTOMER'" not in stmts[0]
    # Nothing else moves: every other line of the file is byte for byte the same.
    changed = [a for a, b in zip(text.splitlines(), rendered.splitlines(), strict=True) if a != b]
    assert len(changed) == 2 and all("is_won" in line for line in changed)
    with pytest.raises(ValueError, match="no won_stages marker"):
        render_won_stages(["WON"])("CREATE OR REPLACE VIEW marts.x AS SELECT 1")


def test_a_bad_stage_stops_the_run_before_anything_is_loaded() -> None:
    class NeverTouched:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"the pipeline was used ({name}) before the stages were checked")

    with pytest.raises(ValueError, match="is not a stage name"):
        run_daily(cast(dlt.Pipeline, NeverTouched()), won=["WON'; DROP TABLE x"])


def test_the_command_takes_the_stages_from_the_flag_then_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(twenty_pipeline, "build_pipeline", lambda: "pipeline")
    monkeypatch.setattr(twenty_pipeline, "run_daily", lambda pipeline, won: seen.append(won) or {})
    configured: dict[str, Any] = {}

    class Config:
        @staticmethod
        def get(key: str, _type: Any) -> Any:
            return configured.get(key)

    monkeypatch.setattr(dlt, "config", Config())
    assert twenty_pipeline.main(["daily"]) == 0
    configured["sources.twenty.won_stages"] = ["WON", "RENEWED"]
    assert twenty_pipeline.main(["daily"]) == 0
    assert twenty_pipeline.main(["daily", "--won-stage", "CLOSED_WON"]) == 0
    assert seen == [("CUSTOMER",), ("WON", "RENEWED"), ("CLOSED_WON",)]


def test_the_won_metrics_read_the_flag_not_a_stage_name() -> None:
    catalog = load_catalog()
    assert catalog.metrics["won_opportunities"].expr == "countIf(is_won)"
    assert catalog.metrics["won_amount"].expr == "sumIf(amount, is_won)"
    assert "is_won" in catalog.reports["twenty.opportunities"].dimensions
    assert "is_won" in catalog.metrics["opportunities"].grains
    # Splitting the won count by the flag or the stage it is made of would only restate the filter.
    assert not {"is_won", "stage"} & set(catalog.metrics["won_opportunities"].grains)
