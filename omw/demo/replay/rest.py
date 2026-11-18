"""Replay handlers for Stripe, LinkedIn's Marketing API and Twenty's REST API.

Stripe: list endpoints, newest first, paged with ``limit`` and ``starting_after``, filtered by
``created[gte]``. LinkedIn: the entity finders and ``adAnalytics`` / ``leadFormResponses`` with their
Rest.li date expressions. Twenty: one endpoint per object, ordered and filtered by ``updatedAt`` (or
``deletedAt`` for the deleted pass) and paged with the ``starting_after`` cursor.
"""

from __future__ import annotations

import re
from typing import Any

from omw.demo.replay.transport import Answer, Call, Fixtures, Handler, not_found

STRIPE_HOST = "api.stripe.com"
LINKEDIN_HOST = "api.linkedin.com"

STRIPE_RESOURCES = ("customers", "subscriptions", "invoices", "balance_transactions")


def stripe(fixtures: Fixtures) -> Handler:
    def handle(call: Call) -> Answer:
        resource = call.path.removeprefix("/v1/")
        if call.method != "GET" or resource not in STRIPE_RESOURCES:
            return not_found(call)
        listing = fixtures.load("stripe", resource)
        data: list[dict[str, Any]] = listing["data"]
        since = call.query.get("created[gte]")
        if since is not None:
            data = [obj for obj in data if obj["created"] >= int(since)]
        after = call.query.get("starting_after")
        if after is not None:
            position = next((n for n, obj in enumerate(data) if obj["id"] == after), None)
            if position is None:
                return 400, {
                    "error": {"type": "invalid_request_error", "message": f"No such object: {after}"}
                }
            data = data[position + 1 :]
        limit = int(call.query.get("limit", 10))
        return 200, {**listing, "data": data[:limit], "has_more": len(data) > limit}

    return handle


_DAY = r"\(year:(\d+),month:(\d+),day:(\d+)\)"
_DATE_RANGE = re.compile(rf"dateRange=\(start:{_DAY},end:{_DAY}\)")
_SUBMITTED = re.compile(r"submittedAtTimeRange=\(start:(\d+),end:(\d+)\)")
_ACCOUNTS = re.compile(r"sponsoredAccount:(\d+)")
_CAMPAIGNS = re.compile(r"campaigns=List\(([^)]*)\)")
_ACCOUNT_PATH = re.compile(r"/rest/adAccounts/(?P<account>\d+)(?:/(?P<child>\w+))?$")
LINKEDIN_CHILDREN = {
    "adCampaignGroups": "campaign_groups",
    "adCampaigns": "campaigns",
    "creatives": "creatives",
}


def _paged(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {"elements": elements, "paging": {"start": 0, "count": len(elements), "links": []}}


def linkedin(fixtures: Fixtures) -> Handler:
    def elements(name: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = fixtures.load("linkedin_ads", name)["elements"]
        return found

    def analytics(call: Call) -> Answer:
        window = _DATE_RANGE.search(call.raw_query)
        accounts = set(_ACCOUNTS.findall(call.raw_query))
        if not window or not accounts <= {str(a["id"]) for a in elements("ad_accounts")}:
            return 400, {"status": 400, "message": "adAnalytics needs a dateRange and a known account"}
        first, last = tuple(int(p) for p in window.groups()[:3]), tuple(int(p) for p in window.groups()[3:])
        creative = "pivot=CREATIVE" in call.raw_query
        rows = elements("ad_analytics_creatives" if creative else "ad_analytics")
        rows = [
            r
            for r in rows
            if first <= tuple(r["dateRange"]["start"][k] for k in ("year", "month", "day")) <= last
        ]
        facet = _CAMPAIGNS.search(call.raw_query)
        if facet:
            wanted = set(facet[1].split(","))
            if creative:
                wanted = {c["id"] for c in elements("creatives") if c["campaign"] in wanted}
            rows = [r for r in rows if r["pivotValues"][0] in wanted]
        return 200, _paged(rows)

    def leads(call: Call) -> Answer:
        window = _SUBMITTED.search(call.raw_query)
        if not window:
            return 400, {"status": 400, "message": "leadFormResponses needs submittedAtTimeRange"}
        rows = [
            r for r in elements("lead_form_responses") if int(window[1]) <= r["submittedAt"] <= int(window[2])
        ]
        return 200, _paged(rows)

    def handle(call: Call) -> Answer:
        if call.method != "GET":
            return not_found(call)
        if call.path == "/rest/adAnalytics":
            return analytics(call)
        if call.path == "/rest/leadFormResponses":
            return leads(call)
        if call.path == "/rest/adAccounts":
            return 200, _paged(elements("ad_accounts"))
        found = _ACCOUNT_PATH.search(call.path)
        if not found:
            return not_found(call)
        account = next((a for a in elements("ad_accounts") if str(a["id"]) == found["account"]), None)
        if account is None:
            return 404, {"status": 404, "message": f"no ad account {found['account']}"}
        if found["child"] is None:
            return 200, account
        name = LINKEDIN_CHILDREN.get(found["child"])
        if name is None:
            return not_found(call)
        urn = f"urn:li:sponsoredAccount:{found['account']}"
        return 200, _paged([e for e in elements(name) if e["account"] == urn])

    return handle


TWENTY_OBJECTS = {
    "companies": "companies",
    "people": "people",
    "opportunities": "opportunities",
    "notes": "notes",
    "tasks": "tasks",
    "noteTargets": "note_targets",
    "taskTargets": "task_targets",
    "timelineActivities": "timeline_activities",
}
_SINCE = re.compile(r'(updatedAt|deletedAt)\[gte\]:"([^"]+)"')


def _instant(text: str) -> str:
    """Twenty's two timestamp spellings (with and without milliseconds) in one comparable form."""
    return text.replace(".000Z", "Z")


def twenty(fixtures: Fixtures) -> Handler:
    def handle(call: Call) -> Answer:
        path = call.path.removeprefix("/rest/")
        if call.method != "GET" or path not in TWENTY_OBJECTS:
            return not_found(call)
        records: list[dict[str, Any]] = fixtures.load("twenty", TWENTY_OBJECTS[path])["data"][path]
        criteria = call.query.get("filter", "")
        deleted = "deletedAt[is]:NOT_NULL" in criteria
        field = "deletedAt" if deleted else "updatedAt"
        records = [r for r in records if (r["deletedAt"] is not None) == deleted]
        since = _SINCE.search(criteria)
        if since:
            records = [r for r in records if _instant(r[since[1]]) >= _instant(since[2])]
        records = sorted(records, key=lambda r: (r[field], r["id"]))
        after = call.query.get("starting_after")
        if after:
            position = next((n for n, r in enumerate(records) if r["id"] == after), -1)
            records = records[position + 1 :]
        limit = int(call.query.get("limit", 60))
        page = records[:limit]
        info = {
            "hasNextPage": len(records) > limit,
            "startCursor": page[0]["id"] if page else None,
            "endCursor": page[-1]["id"] if page else None,
        }
        return 200, {"data": {path: page}, "pageInfo": info, "totalCount": len(records)}

    return handle
