"""Lead-gen form responses (Lead Sync API, ``leadFormResponses``), merged on id over a trailing window.

Needs the ``r_marketing_leadgen_automation`` permission. The answers stay in ``raw`` as JSON; the marts
view only counts leads per campaign and day, so the agent never sees personal data.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, time
from typing import Any

import dlt
from dlt.common.schema.typing import TColumnSchema
from dlt.destinations.adapters import clickhouse_adapter
from dlt.extract import DltResource
from dlt.sources.helpers.rest_client.client import RESTClient

from omw.sources.columns import column
from omw.sources.linkedin_ads.shapes import SOURCE, stamp, urn_id
from omw.sources.windows import Window

_FORM_ID = re.compile(r"urn:li:leadGenForm:(\d+)")


def epoch_ms(day: Any, end: bool = False) -> int:
    moment = datetime.combine(day, time.max if end else time.min, tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def lead_responses_path(account_id: str, window: Window) -> str:
    """The Rest.li query string for one account's sponsored leads submitted inside ``window``."""
    return (
        "leadFormResponses?q=owner"
        f"&owner=(sponsoredAccount:urn%3Ali%3AsponsoredAccount%3A{account_id})"
        "&leadType=(leadType:SPONSORED)"
        f"&submittedAtTimeRange=(start:{epoch_ms(window.start)},end:{epoch_ms(window.end, end=True)})"
    )


def shape_lead_response(account_id: str, obj: Mapping[str, Any]) -> dict[str, Any]:
    entity = obj.get("associatedEntity") or {}
    answers = (obj.get("formResponse") or {}).get("answers") or []
    form_urn = str(obj.get("versionedLeadGenFormUrn") or "")
    match = _FORM_ID.search(form_urn)
    submitted = stamp(obj.get("submittedAt"))
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "account_id": account_id,
        "lead_type": str(obj.get("leadType") or ""),
        "submitted_at": submitted,
        "date": submitted.date(),
        "form_urn": form_urn,
        "form_id": match.group(1) if match else "",
        "campaign_id": urn_id(entity.get("campaign")),
        "creative_id": urn_id(entity.get("creative")),
        "answers": json.dumps(answers, separators=(",", ":")),
        "answer_count": len(answers),
    }


LEAD_RESPONSE_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "account_id": column("text"),
    "lead_type": column("text"),
    "submitted_at": column("timestamp"),
    "date": column("date"),
    "form_urn": column("text"),
    "form_id": column("text"),
    "campaign_id": column("text"),
    "creative_id": column("text"),
    "answers": column("text"),
    "answer_count": column("bigint"),
}


def lead_responses_resource(client: RESTClient, account_ids: Sequence[str], window: Window) -> DltResource:
    def rows() -> Iterator[list[dict[str, Any]]]:
        for account_id in account_ids:
            path = lead_responses_path(account_id, window)
            for page in client.paginate(path, data_selector="elements"):
                yield [shape_lead_response(account_id, item) for item in page]

    resource = dlt.resource(
        rows,
        name="lead_form_responses",
        write_disposition="merge",
        primary_key="id",
        columns=LEAD_RESPONSE_COLUMNS,
    )
    return clickhouse_adapter(resource, sort=["id"])
