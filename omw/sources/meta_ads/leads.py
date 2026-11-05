"""Lead ads: the leads submitted on each ad (``/<ad_id>/leads``), merged on id, incremental on creation time.

Needs the ``leads_retrieval`` permission on the token. The answers (``field_data``) stay in ``raw`` as JSON;
the marts view only counts leads, so the agent never sees personal data.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
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


def leads_resource(
    accounts: Mapping[str, Any],
    page_size: int,
    paced: Callable[[str, Iterator[Any]], Iterator[Any]],
    lead_fetcher: LeadFetcher = sdk_lead_fetcher,
) -> DltResource:
    """One ``leads`` resource: every ad of every account, leads created since the last run."""

    def rows(since: datetime) -> Iterator[list[dict[str, Any]]]:
        params = lead_params(since, page_size)
        for account_id, account in accounts.items():
            ads = account.get_ads(fields=["id"], params={"limit": page_size})
            for ad in ads:
                ad_id = str(export(ad)["id"])
                page = [shape_lead(account_id, item) for item in lead_fetcher(account, ad_id, dict(params))]
                if page:
                    yield page

    def leads(
        created_time: dlt.sources.incremental[datetime] = dlt.sources.incremental(  # noqa: B008 - dlt's cursor idiom
            "created_time", initial_value=INITIAL_CREATED_TIME
        ),
    ) -> Iterator[list[dict[str, Any]]]:
        yield from paced("leads", rows(created_time.last_value or INITIAL_CREATED_TIME))

    resource = dlt.resource(
        leads, name="leads", write_disposition="merge", primary_key="id", columns=LEAD_COLUMNS
    )
    return clickhouse_adapter(resource, sort=["id"])
