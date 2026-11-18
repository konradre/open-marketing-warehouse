"""Lead ads: the leads submitted on each ad (``/<ad_id>/leads``), merged on id, each ad asked for the leads
created after its own newest one.

Needs the ``leads_retrieval`` permission on the token. The answers (``field_data``) stay in ``raw`` as JSON;
the marts view only counts leads, so the agent never sees personal data.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import dlt
from dlt.common.schema.typing import TColumnSchema
from dlt.destinations.adapters import clickhouse_adapter
from dlt.extract import DltResource

from omw.sources.columns import column
from omw.sources.meta_ads.helpers import export

SOURCE_NAME = "meta_ads"
LEAD_FIELDS = (
    "id",
    "created_time",
    "ad_id",
    "ad_name",
    "adset_id",
    "adset_name",
    "campaign_id",
    "campaign_name",
    "form_id",
    "is_organic",
    "platform",
    "field_data",
)
INITIAL_CREATED_TIME = datetime(2000, 1, 1, tzinfo=UTC)
CURSOR_OVERLAP = timedelta(hours=1)
"""How far before an ad's newest lead the next pass asks again, for leads that surfaced late."""
STATE_KEY = "asked_through"
"""Resource state: per ad id, the creation time of the newest lead it has returned."""
LeadFetcher = Callable[[Any, str, dict[str, Any]], Iterator[Mapping[str, Any]]]
"""``(account, ad_id, params) -> lead objects``; the SDK by default, a fixture reader in tests."""


def parse_time(value: Any) -> datetime:
    if not value:
        return INITIAL_CREATED_TIME
    return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S%z").astimezone(UTC)


def shape_lead(account_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
    created = parse_time(item.get("created_time"))
    fields = item.get("field_data") or []
    return {
        "source": SOURCE_NAME,
        "account_id": account_id,
        "id": str(item["id"]),
        "created_time": created,
        "date": created.date(),
        "ad_id": str(item.get("ad_id") or ""),
        "ad_name": str(item.get("ad_name") or ""),
        "adset_id": str(item.get("adset_id") or ""),
        "adset_name": str(item.get("adset_name") or ""),
        "campaign_id": str(item.get("campaign_id") or ""),
        "campaign_name": str(item.get("campaign_name") or ""),
        "form_id": str(item.get("form_id") or ""),
        "is_organic": bool(item.get("is_organic", False)),
        "platform": str(item.get("platform") or ""),
        "field_data": json.dumps(fields, separators=(",", ":")),
        "field_count": len(fields),
    }


LEAD_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "account_id": column("text"),
    "id": column("text"),
    "created_time": column("timestamp"),
    "date": column("date"),
    "ad_id": column("text"),
    "ad_name": column("text"),
    "adset_id": column("text"),
    "adset_name": column("text"),
    "campaign_id": column("text"),
    "campaign_name": column("text"),
    "form_id": column("text"),
    "is_organic": column("bool"),
    "platform": column("text"),
    "field_data": column("text"),
    "field_count": column("bigint"),
}


def sdk_lead_fetcher(account: Any, ad_id: str, params: dict[str, Any]) -> Iterator[Mapping[str, Any]]:
    from facebook_business.adobjects.ad import Ad

    for item in Ad(ad_id, api=account.get_api()).get_leads(fields=list(LEAD_FIELDS), params=params):
        yield export(item)


def lead_params(since: datetime, page_size: int) -> dict[str, Any]:
    """Only leads created after the cursor: Meta filters ``time_created`` by epoch seconds."""
    return {
        "limit": page_size,
        "filtering": [{"field": "time_created", "operator": "GREATER_THAN", "value": int(since.timestamp())}],
    }


def cursor(asked: Mapping[str, Any], ad_id: str) -> datetime:
    """The creation time one ad is asked from: its newest lead so far, less the overlap; the beginning
    for an ad that has returned no lead yet."""
    newest = asked.get(ad_id)
    if not newest:
        return INITIAL_CREATED_TIME
    return max(INITIAL_CREATED_TIME, datetime.fromisoformat(str(newest)) - CURSOR_OVERLAP)


def leads_resource(
    accounts: Mapping[str, Any],
    page_size: int,
    paced: Callable[[str, Iterator[Any]], Iterator[Any]],
    lead_fetcher: LeadFetcher = sdk_lead_fetcher,
) -> DltResource:
    """One ``leads`` resource: every ad of every account, each asked for the leads after its own newest.

    Leads are asked ad by ad, so the newest lead of one ad says nothing about the others, and the cursor
    is kept per ad in the resource state. A pass the pacer stopped moves the cursors of the ads it asked
    and leaves the others where they were; a lead that arrives on an ad after it was asked is still newer
    than that ad's cursor. The overlap covers leads that surface late; what is fetched twice is merged on
    the lead id. A state from before (one cursor for all ads, kept by dlt's incremental) is not read: the
    first pass asks every ad from the beginning once, which also restores what that cursor skipped.
    """

    def rows() -> Iterator[list[dict[str, Any]]]:
        asked: dict[str, str] = dlt.current.resource_state().setdefault(STATE_KEY, {})
        for account_id, account in accounts.items():
            ads = account.get_ads(fields=["id"], params={"limit": page_size})
            for ad in ads:
                ad_id = str(export(ad)["id"])
                params = lead_params(cursor(asked, ad_id), page_size)
                page = [shape_lead(account_id, item) for item in lead_fetcher(account, ad_id, params)]
                if page:
                    yield page
                    asked[ad_id] = max(row["created_time"] for row in page).isoformat()

    def leads() -> Iterator[list[dict[str, Any]]]:
        yield from paced("leads", rows())

    resource = dlt.resource(
        leads, name="leads", write_disposition="merge", primary_key="id", columns=LEAD_COLUMNS
    )
    return clickhouse_adapter(resource, sort=["id"])
