"""Daily ``adAnalytics`` at one pivot, read in requests that stay under LinkedIn's response cap.

``adAnalytics`` does not paginate, and a response is limited to 15 000 elements (LinkedIn's reporting
documentation, "Restrictions"). A request that would return more comes back cut off, with nothing in the
response to say so. Daily rows multiply quickly: one row per campaign or creative and day, so a 7-day
re-pull of an account with 2 200 active creatives is already past the cap.

So the paging here is on the request side. A response that reaches the cap is never trusted:

1. a window of several days is split in two and each half is asked again;
2. a single day is asked per batch of campaigns (the campaigns with activity that day, from the campaign
   pivot of the same day), and a batch that still reaches the cap is halved down to one campaign;
3. one campaign on one day at the cap cannot be split further and fails the run, rather than loading a
   slice that is silently short.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from datetime import timedelta
from typing import Any

import dlt
from dlt.common.schema.typing import TMergeDispositionDict
from dlt.destinations.adapters import clickhouse_adapter
from dlt.extract import DltResource
from dlt.sources.helpers.rest_client.client import RESTClient

from omw.sources.linkedin_ads.shapes import (
    ANALYTICS_FIELDS,
    PIVOT_REPORT,
    analytics_columns,
    shape_analytics,
    urn_id,
)
from omw.sources.windows import Window

log = logging.getLogger(__name__)

RESPONSE_CAP = 15_000
"""Elements one ``adAnalytics`` response can hold. A response this long may have been cut off."""
CAMPAIGN_BATCH = 50
"""Campaign urns per request when one day has to be split; keeps the URL well under the length limit."""
MERGE_DISPOSITION: TMergeDispositionDict = {"disposition": "merge", "strategy": "delete-insert"}
ANALYTICS_MERGE_KEY = ("account_id", "date")

Elements = list[dict[str, Any]]
Fetch = Callable[[str], Elements]
"""A request path in, every element of the response out."""


class AnalyticsCapError(RuntimeError):
    """A request that cannot be made any smaller still reached the response cap."""


def restli_date_range(window: Window) -> str:
    s, e = window.start, window.end
    return (
        f"(start:(year:{s.year},month:{s.month},day:{s.day}),end:(year:{e.year},month:{e.month},day:{e.day}))"
    )


def analytics_path(
    account_id: str, window: Window, pivot: str = "CAMPAIGN", campaign_ids: Sequence[str] = ()
) -> str:
    """The Rest.li query string, built by hand: parentheses must stay literal and URN colons encoded.

    ``campaign_ids`` narrows the request to those campaigns of the account (the ``campaigns`` facet)."""
    campaigns = ",".join(f"urn%3Ali%3AsponsoredCampaign%3A{campaign_id}" for campaign_id in campaign_ids)
    return (
        f"adAnalytics?q=analytics&pivot={pivot}&timeGranularity=DAILY"
        f"&accounts=List(urn%3Ali%3AsponsoredAccount%3A{account_id})"
        + (f"&campaigns=List({campaigns})" if campaigns else "")
        + f"&dateRange={restli_date_range(window)}&fields={','.join(ANALYTICS_FIELDS)}"
    )


def _halves(window: Window) -> tuple[Window, Window]:
    middle = window.start + timedelta(days=window.days // 2 - 1)
    return Window(window.start, middle), Window(middle + timedelta(days=1), window.end)


def fetch_analytics(
    fetch: Fetch,
    account_id: str,
    window: Window,
    pivot: str = "CAMPAIGN",
    cap: int = RESPONSE_CAP,
    campaign_ids: Sequence[str] | None = None,
) -> Iterator[Elements]:
    """Every element of ``window`` at ``pivot``, one list per request that came back under ``cap``."""
    elements = fetch(analytics_path(account_id, window, pivot, campaign_ids or ()))
    if len(elements) < cap:
        yield elements
        return
    scope = f"account {account_id}, {window}, pivot {pivot}"
    if window.days > 1:
        log.warning("adAnalytics reached the %d element cap (%s): splitting the window", cap, scope)
        for half in _halves(window):
            yield from fetch_analytics(fetch, account_id, half, pivot, cap, campaign_ids)
        return
    if campaign_ids is None:
        if pivot == "CAMPAIGN":
            raise AnalyticsCapError(
                f"adAnalytics returned {len(elements)} campaign rows for one day ({scope}); the response "
                f"cap is {cap} and a day of the campaign pivot cannot be split further"
            )
        log.warning("adAnalytics reached the %d element cap (%s): splitting by campaign", cap, scope)
        active: set[str] = set()
        for page in fetch_analytics(fetch, account_id, window, "CAMPAIGN", cap):
            active.update(urn_id((item.get("pivotValues") or [""])[0]) for item in page)
        campaigns = sorted(active - {""})
        for start in range(0, len(campaigns), CAMPAIGN_BATCH):
            batch = campaigns[start : start + CAMPAIGN_BATCH]
            yield from fetch_analytics(fetch, account_id, window, pivot, cap, batch)
        return
    if len(campaign_ids) > 1:
        middle = len(campaign_ids) // 2
        for part in (campaign_ids[:middle], campaign_ids[middle:]):
            yield from fetch_analytics(fetch, account_id, window, pivot, cap, part)
        return
    raise AnalyticsCapError(
        f"adAnalytics returned {len(elements)} rows for campaign {campaign_ids[0]} on one day ({scope}); "
        f"the response cap is {cap} and the request cannot be made smaller"
    )


def analytics_resource(
    client: RESTClient,
    account_ids: Sequence[str],
    window: Window,
    pivot: str = "CAMPAIGN",
    cap: int = RESPONSE_CAP,
) -> DltResource:
    """Daily analytics at one pivot: ``CAMPAIGN`` (the ``ad_analytics`` table) or ``CREATIVE``
    (``ad_analytics_creatives``), each replacing its (account, day) slice."""
    pivot_id = "creative_id" if pivot == "CREATIVE" else "campaign_id"

    def fetch(path: str) -> Elements:
        # adAnalytics answers in one response; paginate still raises on an HTTP error and would follow a
        # page token if LinkedIn ever sent one.
        return [item for page in client.paginate(path, data_selector="elements") for item in page]

    def rows() -> Iterator[list[dict[str, Any]]]:
        for account_id in account_ids:
            response = client.get(f"adAccounts/{account_id}")
            response.raise_for_status()  # RESTClient.get does not raise; paginate does
            currency = str(response.json().get("currency") or "")
            for elements in fetch_analytics(fetch, account_id, window, pivot, cap):
                yield [shape_analytics(account_id, currency, item, pivot) for item in elements]

    resource = dlt.resource(
        rows,
        name="ad_analytics" if pivot == "CAMPAIGN" else f"ad_analytics_{PIVOT_REPORT[pivot]}",
        write_disposition=MERGE_DISPOSITION,
        merge_key=list(ANALYTICS_MERGE_KEY),
        columns=analytics_columns(pivot),
    )
    return clickhouse_adapter(resource, sort=[*ANALYTICS_MERGE_KEY, pivot_id])
