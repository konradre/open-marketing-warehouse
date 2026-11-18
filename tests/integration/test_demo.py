"""The demo end to end: generated fixtures through the real pipelines into ClickHouse, a daily sync on
top, the marts, the checks and the five answers read back as `agent`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from clickhouse_connect.driver.client import Client

from omw.demo import warehouse
from omw.demo.cli import answers, check, daily_sync, first_load, unexpected, unexpected_checks
from omw.demo.examples import FAULT_EXAMPLES
from omw.demo.fixtures import SOURCES, Manifest, generate
from omw.demo.keys import shared_keys
from omw.demo.load import Run, load_snapshot
from omw.demo.replay import Replay
from omw.demo.restatement import Change
from omw.demo.world import CAMPAIGN_KEYS, META_ACCOUNT
from omw.metrics import Mood

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 18)
DAYS = 60
TABLE_OF_FILE = {
    "campaign_entities": "campaigns",
    "campaigns_stream": "performance_campaigns",
    "ad_groups_stream": "performance_ad_groups",
    "search_terms_stream": "performance_search_terms",
    "asset_groups_stream": "performance_asset_groups",
}
"""Google Ads fixture files -> the raw tables they load into; every other file is named like its table."""
DATED = ("search_console", "meta_ads", "google_ads", "ga4", "linkedin_ads")


@dataclass
class Loaded:
    out: Path
    manifest: Manifest
    first: dict[str, tuple[int, int]]
    changes: list[Change]


@pytest.fixture(scope="module")
def loaded(pipeline_client: Client, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Loaded]:
    out = tmp_path_factory.mktemp("demo")
    manifest = generate(out, TODAY, DAYS)
    warehouse.reset(pipeline_client, force=True)  # whatever an earlier module or run left behind
    pipelines_dir = str(tmp_path_factory.mktemp("dlt"))
    first_load(out, manifest, pipelines_dir, force=False)
    first = warehouse.row_counts(pipeline_client)
    changes = daily_sync(out, manifest, pipelines_dir)
    yield Loaded(out, manifest, first, changes)
    warehouse.reset(pipeline_client, force=True)


def _count(client: Client, table: str) -> int:
    return int(client.query(f"SELECT count() FROM {table}").result_rows[0][0])


def test_the_first_load_holds_the_initial_snapshot(loaded: Loaded) -> None:
    records = loaded.manifest.data["records"]["initial"]
    assert set(loaded.first) == set(SOURCES)
    for source, (_tables, rows) in loaded.first.items():
        expected = sum(count for name, count in records[source].items() if name != "account")
        assert rows == expected, source


def test_after_the_daily_sync_the_warehouse_equals_the_restated_snapshot(
    loaded: Loaded, pipeline_client: Client
) -> None:
    """Row for row: a re-fetched slice replaced what was there, nothing was appended twice or lost."""
    for source, files in loaded.manifest.data["records"]["restated"].items():
        for name, rows in files.items():
            if name == "account":  # the Meta account object is read for its currency, not loaded
                continue
            table = f"raw.{source}___{TABLE_OF_FILE.get(name, name)}"
            assert _count(pipeline_client, table) == rows, table


def test_the_daily_sync_restated_days_without_duplicating_any(loaded: Loaded) -> None:
    by_source = {change.watch.source: change for change in loaded.changes}
    assert set(by_source) == set(SOURCES)
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    for source, change in by_source.items():
        assert change.after.duplicates == 0, source
        assert change.after.rows >= change.before.rows, source  # a quiet day adds no CRM or billing record
    for source in DATED:
        assert by_source[source].after.rows > by_source[source].before.rows, source
        assert by_source[source].new == [yesterday], source
        assert by_source[source].restated, source
        assert min(by_source[source].restated) >= (TODAY - timedelta(days=10)).isoformat(), source


def test_restated_numbers_are_the_ones_in_the_warehouse(loaded: Loaded, agent_client: Client) -> None:
    fixture = json.loads((loaded.out / "restated" / "meta_ads" / "insights_campaigns.json").read_text())[
        "data"
    ]
    day = (TODAY - timedelta(days=2)).isoformat()  # loaded short yesterday, restated by the daily sync
    expected = sum(Decimal(row["spend"]) for row in fixture if row["date_start"] == day)
    spend = agent_client.query(
        "SELECT sum(spend) FROM marts.meta_ads_campaigns WHERE date = {d:Date} AND account_id = {a:String}",
        parameters={"d": day, "a": META_ACCOUNT},
    ).result_rows[0][0]
    assert Decimal(str(spend)) == expected > 0
    first = json.loads((loaded.out / "initial" / "meta_ads" / "insights_campaigns.json").read_text())["data"]
    assert sum(Decimal(row["spend"]) for row in first if row["date_start"] == day) < expected


def test_one_campaign_key_spans_the_ad_platforms_ga4_stripe_and_twenty(
    loaded: Loaded, pipeline_client: Client
) -> None:
    columns, rows = shared_keys(pipeline_client)
    by_key = {row[0]: dict(zip(columns, row, strict=True)) for row in rows}
    assert set(by_key) == {*CAMPAIGN_KEYS, "(no key)"}
    widgets = by_key["widgets_prospecting"]
    assert (widgets["meta_ads"], widgets["google_ads"], widgets["linkedin_ads"]) == (
        "Widgets - Prospecting",
        "Widgets | Prospecting",
        "Widgets Prospecting",
    )
    assert widgets["ga4 sessions"] > 1000 and widgets["stripe customers"] > 10
    assert by_key["abm_tier_1"]["linkedin_ads"] == "LI | ABM Tier 1 *"  # the key set by hand
    assert by_key["abm_tier_1"]["twenty opportunities"] + by_key["lead_gen_ebook"]["twenty opportunities"] > 5
    assert by_key["(no key)"]["stripe customers"] > 10  # organic customers stay unattributed


def test_every_post_sync_check_passes(loaded: Loaded) -> None:
    run = check(loaded.manifest)
    assert not run.failed and not run.warned, [(r.source, r.check, r.report, r.detail) for r in run.results]
    assert {result.source for result in run.results} == set(SOURCES)
    assert {result.check for result in run.results} == {"freshness", "overcount", "rowcount"}


def test_the_five_answers_cover_every_mood_with_real_numbers(loaded: Loaded) -> None:
    check(loaded.manifest)
    found = answers(loaded.manifest)
    assert unexpected(found) == []
    assert {answer["mood"] for _, answer in found} == set(Mood)
    devices, meta, roas, clarify, refuse = (answer for _, answer in found)
    assert {row[0] for row in devices["rows"]} == {"DESKTOP", "MOBILE", "TABLET"} and not devices["caveats"]
    assert {row[0] for row in meta["rows"]} == {
        "widgets_prospecting",
        "widgets_retargeting",
        "gadgets_launch",
        "lead_gen_ebook",
    }
    assert any("28-day restatement window" in caveat for caveat in meta["caveats"])
    by_key: dict[str, list[Any]] = {row[0]: row for row in roas["rows"]}
    assert roas["columns"] == ["campaign_key", "ad_spend", "revenue", "roas"]
    assert float(by_key["widgets_prospecting"][3]) > 0 and float(by_key["brand_search"][3]) > 0
    assert float(by_key[""][2]) > 0  # unattributed revenue is real revenue, with no spend to set against it
    assert "emea_generic" not in by_key  # a EUR account: money is never added across currencies
    assert [c["name"] for c in clarify["candidates"]] == ["google_spend", "linkedin_spend", "meta_spend"]
    assert "not additive" in refuse["reason"] and not refuse["rows"]


def test_the_agent_reads_the_demo_through_marts_only(loaded: Loaded, agent_client: Client) -> None:
    assert _count(agent_client, "marts.twenty_pipeline") > 20
    lost = agent_client.query(
        "SELECT countIf(is_lost), countIf(is_won) FROM marts.twenty_pipeline"
    ).result_rows[0]
    assert lost[0] > 0 and lost[1] > 0  # the demo configures LOST as the closed-lost stage
    with pytest.raises(Exception, match="ACCESS_DENIED|Not enough privileges"):
        agent_client.query("SELECT count() FROM raw.stripe___customers")


def test_a_balance_transaction_loaded_as_pending_is_available_once_its_day_has_come(
    loaded: Loaded, pipeline_client: Client, agent_client: Client
) -> None:
    """Stripe moves a balance transaction from pending to available after the pipeline has loaded it, and
    the pipeline never fetches it again. The view goes by ``available_on`` instead."""
    stale = "countIf(status = 'pending' AND available_on <= now())"
    raw = pipeline_client.query(
        f"SELECT {stale}, count() FROM raw.stripe___balance_transactions"
    ).result_rows[0]
    assert raw[0] > 0  # loaded while pending, due since
    marts = agent_client.query(f"SELECT {stale}, count() FROM marts.stripe_balance").result_rows[0]
    assert (marts[0], marts[1]) == (0, raw[1])


def test_the_demo_only_replaces_what_it_loaded_itself(
    loaded: Loaded, pipeline_client: Client, admin_client: Client
) -> None:
    assert warehouse.owned(pipeline_client)
    # Something else loads into raw after the demo: a real sync would add a row to a dlt loads table.
    admin_client.command(
        "INSERT INTO raw.`stripe____dlt_loads` (load_id, schema_name, status, inserted_at) "
        "VALUES ('9999999999.1', 'stripe', 0, now())"
    )
    try:
        assert not warehouse.owned(pipeline_client)
        with pytest.raises(warehouse.DemoRefusedError, match="did not load"):
            warehouse.reset(pipeline_client)
        with pytest.raises(warehouse.DemoRefusedError, match="omw demo load --snapshot initial"):
            daily_sync(loaded.out, loaded.manifest, "unused")
    finally:
        admin_client.command("DELETE FROM raw.`stripe____dlt_loads` WHERE load_id = '9999999999.1'")
    assert warehouse.owned(pipeline_client)


def test_a_second_run_replaces_the_demo_data_without_force(loaded: Loaded, pipeline_client: Client) -> None:
    assert warehouse.reset(pipeline_client) > 50
    assert warehouse.source_tables(pipeline_client) == []
    assert warehouse.recorded(pipeline_client) is None


def test_a_planted_overcount_fails_its_check_and_is_disclosed_with_the_answer(
    pipeline_client: Client, tmp_path: Path
) -> None:
    """Runs last: the module's warehouse is gone by now, and GA4 alone is enough to see the fault."""
    manifest = generate(tmp_path / "fault", TODAY, DAYS, fault="overcount")
    warehouse.reset(pipeline_client, force=True)
    replay = Replay(tmp_path / "fault" / "initial", manifest.data["accounts"])
    run = Run(manifest, "initial", replay, warehouse.destination(), str(tmp_path / "dlt"), first=True)
    try:
        load_snapshot(run, sources=("ga4",))
        checks = check(manifest)
        ga4 = {(r.check, r.report): r for r in checks.results if r.source == "ga4"}
        assert ga4[("overcount", "channels")].failed
        assert "3 of" in ga4[("overcount", "channels")].detail
        assert not [key for key, result in ga4.items() if result.failed and key != ("overcount", "channels")]
        assert not [text for text in unexpected_checks(manifest, checks) if text.startswith("ga4/")]
        found = answers(manifest)
        example, answer = found[-1]
        assert example is FAULT_EXAMPLES["overcount"] and answer["rows"]
        assert any("ga4.channels overcount check fail" in caveat for caveat in answer["caveats"])
    finally:
        warehouse.reset(pipeline_client, force=True)
