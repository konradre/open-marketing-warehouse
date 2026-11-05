"""The dlt source: ``rest_api`` resources for the entities, a ``RESTClient`` resource for the analytics."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import Any

import dlt
import requests
from dlt.common.schema.typing import TMergeDispositionDict
from dlt.destinations.adapters import clickhouse_adapter
from dlt.extract import DltResource
from dlt.sources.helpers.requests import Client as RetryingClient
from dlt.sources.helpers.rest_client.client import RESTClient
from dlt.sources.helpers.rest_client.paginators import JSONResponseCursorPaginator
from dlt.sources.rest_api import rest_api_resources
from dlt.sources.rest_api.typing import RESTAPIConfig

from omw.sources.columns import column
from omw.sources.linkedin_ads.auth import LinkedInAuth
from omw.sources.linkedin_ads.shapes import (
    ACCOUNT_COLUMNS,
    ANALYTICS_COLUMNS,
    ANALYTICS_FIELDS,
    CAMPAIGN_COLUMNS,
    CAMPAIGN_GROUP_COLUMNS,
    CREATIVE_COLUMNS,
    shape_account,
    shape_analytics,
    shape_campaign,
    shape_campaign_group,
    shape_creative,
)
from omw.sources.linkedin_ads.version import LINKEDIN_VERSION
from omw.sources.windows import Window, trailing_window

log = logging.getLogger(__name__)

SOURCE_NAME = "linkedin_ads"
API_BASE = "https://api.linkedin.com/rest/"
RE_PULL_DAYS = 7
"""LinkedIn settles conversions and cost over a few days; a week covers it."""
PAGE_SIZE = 100
MERGE_DISPOSITION: TMergeDispositionDict = {"disposition": "merge", "strategy": "delete-insert"}
ANALYTICS_MERGE_KEY = ("account_id", "date")


def paginator() -> JSONResponseCursorPaginator:
    """LinkedIn's cursor paging: ``metadata.nextPageToken`` in, ``pageToken`` out."""
    return JSONResponseCursorPaginator(cursor_path="metadata.nextPageToken", cursor_param="pageToken")


def default_session(
    auth: LinkedInAuth, max_attempts: int = 5, backoff_factor: float = 1.0
) -> requests.Session:
    """One session for every call: bearer + version headers from ``auth``, bounded retries on 429/5xx."""
    session = RetryingClient(
        request_timeout=60,
        request_max_attempts=max_attempts,
        request_backoff_factor=backoff_factor,
        raise_for_status=False,  # RESTClient raises itself; it warns when the session also does
    ).session
    session.auth = auth
    return session


def entity_resources(session: requests.Session, account_ids: Sequence[str]) -> list[DltResource]:
    """Ad accounts, campaign groups, campaigns and creatives through dlt's declarative rest_api."""
    resolve_account = {"type": "resolve", "resource": "ad_accounts", "field": "id"}
    config: RESTAPIConfig = {
        "client": {"base_url": API_BASE, "session": session, "paginator": paginator()},
        "resource_defaults": {
            "write_disposition": "merge",
            "primary_key": "id",
            "endpoint": {"params": {"pageSize": PAGE_SIZE}, "data_selector": "elements"},
        },
        "resources": [
            {
                "name": "ad_accounts",
                "endpoint": {
                    "path": "adAccounts",
                    "params": {"q": "search", "search": f"(id:(values:List({','.join(account_ids)})))"},
                },
                "columns": ACCOUNT_COLUMNS,
                "processing_steps": [{"map": shape_account}],
            },
            {
                "name": "campaign_groups",
                "endpoint": {
                    "path": "adAccounts/{account_id}/adCampaignGroups",
                    "params": {"q": "search", "account_id": resolve_account},
                },
                "columns": CAMPAIGN_GROUP_COLUMNS,
                "processing_steps": [{"map": shape_campaign_group}],
            },
            {
                "name": "campaigns",
                "endpoint": {
                    "path": "adAccounts/{account_id}/adCampaigns",
                    "params": {"q": "search", "account_id": resolve_account},
                },
                "columns": CAMPAIGN_COLUMNS,
                "processing_steps": [{"map": shape_campaign}],
            },
            {
                "name": "creatives",
                "endpoint": {
                    "path": "adAccounts/{account_id}/creatives",
                    "params": {"q": "criteria", "account_id": resolve_account},
                },
                "columns": CREATIVE_COLUMNS,
                "processing_steps": [{"map": shape_creative}],
            },
        ],
    }
    return [clickhouse_adapter(resource, sort=["id"]) for resource in rest_api_resources(config)]


def restli_date_range(window: Window) -> str:
    s, e = window.start, window.end
    return (
        f"(start:(year:{s.year},month:{s.month},day:{s.day}),end:(year:{e.year},month:{e.month},day:{e.day}))"
    )


def analytics_path(account_id: str, window: Window) -> str:
    """The Rest.li query string, built by hand: parentheses must stay literal and URN colons encoded."""
    return (
        "adAnalytics?q=analytics&pivot=CAMPAIGN&timeGranularity=DAILY"
        f"&accounts=List(urn%3Ali%3AsponsoredAccount%3A{account_id})"
        f"&dateRange={restli_date_range(window)}&fields={','.join(ANALYTICS_FIELDS)}"
    )


def analytics_resource(client: RESTClient, account_ids: Sequence[str], window: Window) -> DltResource:
    def rows() -> Iterator[list[dict[str, Any]]]:
        for account_id in account_ids:
            response = client.get(f"adAccounts/{account_id}")
            response.raise_for_status()  # RESTClient.get does not raise; paginate does
            currency = str(response.json().get("currency") or "")
            for page in client.paginate(analytics_path(account_id, window), data_selector="elements"):
                yield [shape_analytics(account_id, currency, item) for item in page]

    resource = dlt.resource(
        rows,
        name="ad_analytics",
        write_disposition=MERGE_DISPOSITION,
        merge_key=list(ANALYTICS_MERGE_KEY),
        columns=ANALYTICS_COLUMNS,
    )
    return clickhouse_adapter(resource, sort=[*ANALYTICS_MERGE_KEY, "campaign_id"])


def sync_windows_resource(account_ids: Sequence[str], window: Window) -> DltResource:
    def rows() -> Iterator[dict[str, Any]]:
        loaded_at = datetime.now(tz=UTC)
        for account_id in account_ids:
            yield {
                "account_id": account_id,
                "mode": "daily",
                "window_start": window.start,
                "window_end": window.end,
                "loaded_at": loaded_at,
            }

    resource = dlt.resource(
        rows,
        name="sync_windows",
        write_disposition="append",
        columns={
            "account_id": column("text"),
            "mode": column("text"),
            "window_start": column("date"),
            "window_end": column("date"),
            "loaded_at": column("timestamp"),
        },
    )
    return clickhouse_adapter(resource, sort=["account_id", "window_start"])


@dlt.source(name=SOURCE_NAME, max_table_nesting=0)
def linkedin_ads(
    account_ids: Sequence[str] = dlt.config.value,
    client_id: str | None = dlt.secrets.value,
    client_secret: str | None = dlt.secrets.value,
    refresh_token: str | None = dlt.secrets.value,
    version: str = LINKEDIN_VERSION,
    window: Window | None = None,
    days: int = RE_PULL_DAYS,
    auth: LinkedInAuth | None = None,
    session: requests.Session | None = None,
    today: date | None = None,
) -> Iterator[DltResource]:
    """LinkedIn Ads entities and daily campaign analytics for ``window`` (default: last ``days`` days).

    ``account_ids`` are numeric sponsored account ids. ``auth`` is injectable for tests; otherwise the
    OAuth refresh-token flow from the secrets.
    """
    if not account_ids:
        raise ValueError("linkedin_ads needs at least one account id (sources.linkedin_ads.account_ids)")
    accounts = [str(a) for a in account_ids]
    if auth is None:
        if not (client_id and client_secret and refresh_token):
            raise ValueError(
                "linkedin_ads needs client_id, client_secret and refresh_token (sources.linkedin_ads.*)"
            )
        auth = LinkedInAuth(
            version, client_id=client_id, client_secret=client_secret, refresh_token=refresh_token
        )
    window = window or trailing_window(today or date.today(), days)
    session = session or default_session(auth)
    client = RESTClient(base_url=API_BASE, session=session, paginator=paginator())

    yield from entity_resources(session, accounts)
    yield analytics_resource(client, accounts, window)
    yield sync_windows_resource(accounts, window)
