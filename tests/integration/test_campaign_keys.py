"""``omw campaign-keys sync --dry-run`` against ClickHouse as the pipeline user: the plan matches what the
sync then writes, and planning leaves the warehouse exactly as it was."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from clickhouse_connect.driver.client import Client

from omw.metrics.campaign_keys import Action, plan_campaign_keys, set_campaign_key, sync_campaign_keys
from tests.integration.conftest import connect, setting

pytestmark = pytest.mark.integration

CAMPAIGNS = "raw.linkedin_ads___campaigns"


def _campaigns(admin_client: Client, rows: list[list[str]]) -> None:
    admin_client.command(f"DROP TABLE IF EXISTS {CAMPAIGNS}")
    admin_client.command(f"CREATE TABLE {CAMPAIGNS} (id String, name String) ENGINE = MergeTree ORDER BY id")
    admin_client.insert(CAMPAIGNS, rows, column_names=["id", "name"])


@pytest.fixture
def pipeline_client(admin_client: Client) -> Iterator[Client]:
    admin_client.command("DROP TABLE IF EXISTS marts.campaign_keys")
    _campaigns(
        admin_client,
        [["800001", "Widgets - Sponsored Content"], ["800002", "Gadgets - Video"], ["800003", "--"]],
    )
    client = connect("pipeline", setting("OMW_PIPELINE_PASSWORD"))
    yield client
    client.close()
    admin_client.command(f"DROP TABLE IF EXISTS {CAMPAIGNS}")
    admin_client.command("DROP TABLE IF EXISTS marts.campaign_keys")


def _actions(client: Client) -> dict[str, tuple[Action, str, str]]:
    plan = plan_campaign_keys(client, sources={"linkedin_ads"})
    return {p.native_id: (p.action, p.current_key, p.campaign_key) for p in plan}


def _mapping_rows(admin_client: Client) -> int:
    return int(admin_client.command("SELECT count() FROM marts.campaign_keys"))


def test_the_dry_run_matches_the_sync_and_writes_nothing(
    pipeline_client: Client, admin_client: Client
) -> None:
    # Before the first sync the mapping table does not exist. The plan is all additions and creates nothing.
    assert _actions(pipeline_client) == {
        "800001": (Action.ADD, "", "widgets_sponsored_content"),
        "800002": (Action.ADD, "", "gadgets_video"),
        "800003": (Action.NO_KEY, "", ""),
    }
    assert admin_client.command("EXISTS TABLE marts.campaign_keys") == 0

    assert sync_campaign_keys(pipeline_client, sources={"linkedin_ads"}) == {"linkedin_ads": 2}
    assert _actions(pipeline_client) == {
        "800001": (Action.UNCHANGED, "widgets_sponsored_content", "widgets_sponsored_content"),
        "800002": (Action.UNCHANGED, "gadgets_video", "gadgets_video"),
        "800003": (Action.NO_KEY, "", ""),
    }
    assert _mapping_rows(admin_client) == 2

    # One campaign is renamed on the platform, one is mapped by hand: the plan names the change and the
    # protected row, and still writes nothing.
    set_campaign_key(pipeline_client, "linkedin_ads", "800002", "gadgets_q4")
    _campaigns(
        admin_client,
        [["800001", "Widgets - Sponsored Content Q4"], ["800002", "Gadgets - Video"], ["800003", "--"]],
    )
    before = _mapping_rows(admin_client)
    assert _actions(pipeline_client) == {
        "800001": (Action.CHANGE, "widgets_sponsored_content", "widgets_sponsored_content_q4"),
        "800002": (Action.MANUAL, "gadgets_q4", "gadgets_video"),
        "800003": (Action.NO_KEY, "", ""),
    }
    assert _mapping_rows(admin_client) == before

    # The sync then writes exactly the planned change, and the next plan has nothing left to do.
    assert sync_campaign_keys(pipeline_client, sources={"linkedin_ads"}) == {"linkedin_ads": 1}
    assert [action for action, _, _ in _actions(pipeline_client).values()] == [
        Action.UNCHANGED,
        Action.MANUAL,
        Action.NO_KEY,
    ]
