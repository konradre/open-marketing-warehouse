"""Replay handlers for the three Google APIs: Search Console, the GA4 Data API and Google Ads.

Each one answers the request it is given, not a canned page: the date range, the dimensions, the paging
parameters and (for Google Ads) the customer and the GAQL ``FROM`` are honoured, so a backfill chunk, a
restatement window and a paged read all get what the real API would return for that data.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from omw.demo.replay.transport import Answer, Call, Fixtures, Handler, not_found

SEARCH_CONSOLE_HOST = "searchconsole.googleapis.com"
GA4_HOST = "analyticsdata.googleapis.com"
GOOGLE_ADS_HOST = "googleads.googleapis.com"

_SITE = re.compile(r"/webmasters/v3/sites/(?P<site>.+)/searchAnalytics/query$")
_PROPERTY = re.compile(r"/v1beta/properties/(?P<property>\d+):runReport$")
_CUSTOMER = re.compile(r"/v\d+/customers/(?P<customer>\d+)/googleAds:searchStream$")
_FROM = re.compile(r"\bFROM\s+(\w+)")
_BETWEEN = re.compile(r"segments\.date BETWEEN '(\d{4}-\d{2}-\d{2})' AND '(\d{4}-\d{2}-\d{2})'")

SEARCH_GRAINS: dict[tuple[str, ...], str] = {
    (): "core",
    ("query",): "queries",
    ("page",): "pages",
    ("query", "page"): "query_page",
    ("country",): "countries",
    ("device",): "devices",
}
GA4_GRAINS: dict[tuple[str, ...], str] = {
    (): "core",
    ("sessionDefaultChannelGroup",): "channels",
    ("landingPage",): "landing_pages",
    ("sessionCampaignName", "sessionSource", "sessionMedium"): "campaigns",
}
GAQL_RESOURCES = {
    "campaign": "campaigns_stream",
    "ad_group": "ad_groups_stream",
    "search_term_view": "search_terms_stream",
    "asset_group": "asset_groups_stream",
}


def _forbidden(what: str) -> Answer:
    return 403, {
        "error": {"code": 403, "message": f"fixture mode: no access to {what}", "status": "PERMISSION_DENIED"}
    }


def search_console(fixtures: Fixtures, site_urls: Sequence[str]) -> Handler:
    def handle(call: Call) -> Answer:
        found = _SITE.search(call.path)
        if call.method != "POST" or not found:
            return not_found(call)
        if found["site"] not in site_urls:
            return _forbidden(f"site {found['site']}")
        body: dict[str, Any] = call.body or {}
        grain = SEARCH_GRAINS.get(tuple(body.get("dimensions", ["date"])[1:]))
        if grain is None:
            return 400, {
                "error": {"code": 400, "message": f"unsupported dimensions {body.get('dimensions')}"}
            }
        rows = (
            fixtures.load("search_console", grain)["rows"] if body.get("searchType", "web") == "web" else []
        )
        start, end = body["startDate"], body["endDate"]
        rows = [row for row in rows if start <= row["keys"][0] <= end]
        offset, limit = int(body.get("startRow", 0)), int(body.get("rowLimit", 1000))
        page = rows[offset : offset + limit]
        answer: dict[str, Any] = {"responseAggregationType": "byProperty"}
        if page:  # the API leaves `rows` out of an empty answer
            answer["rows"] = page
        return 200, answer

    return handle


def ga4(fixtures: Fixtures, property_ids: Sequence[str]) -> Handler:
    def handle(call: Call) -> Answer:
        found = _PROPERTY.search(call.path)
        if call.method != "POST" or not found:
            return not_found(call)
        if found["property"] not in property_ids:
            return _forbidden(f"property {found['property']}")
        body: dict[str, Any] = call.body or {}
        names = tuple(d["name"] for d in body.get("dimensions", []))
        grain = GA4_GRAINS.get(names[1:]) if names[:1] == ("date",) else None
        if grain is None:
            return 400, {"error": {"code": 400, "message": f"unsupported dimensions {list(names)}"}}
        report = fixtures.load("ga4", grain)
        window = body["dateRanges"][0]
        start, end = window["startDate"].replace("-", ""), window["endDate"].replace("-", "")
        rows = [row for row in report["rows"] if start <= row["dimensionValues"][0]["value"] <= end]
        offset, limit = int(body.get("offset", 0)), int(body.get("limit", 10_000))
        answer = {key: value for key, value in report.items() if key != "rows"}
        answer["rows"] = rows[offset : offset + limit]
        answer["rowCount"] = len(rows)
        answer["propertyQuota"] = {"tokensPerDay": {"consumed": 7, "remaining": 199_993}}
        return 200, answer

    return handle


def google_ads(fixtures: Fixtures, customer_ids: Sequence[str]) -> Handler:
    def handle(call: Call) -> Answer:
        found = _CUSTOMER.search(call.path)
        query = str((call.body or {}).get("query", "")) if isinstance(call.body, dict) else ""
        resource = _FROM.search(query)
        if call.method != "POST" or not found or not resource:
            return not_found(call)
        customer = found["customer"]
        if customer not in customer_ids:
            return _forbidden(f"customer {customer}")
        window = _BETWEEN.search(query)
        name = GAQL_RESOURCES.get(resource[1]) if window else "campaign_entities"
        if name is None:
            return 400, {"error": {"code": 400, "message": f"unsupported GAQL resource {resource[1]}"}}
        chunks = []
        for chunk in fixtures.load("google_ads", name):
            results = [row for row in chunk["results"] if row["customer"]["id"] == customer]
            if window:
                results = [row for row in results if window[1] <= row["segments"]["date"] <= window[2]]
            if results:
                chunks.append({**chunk, "results": results})
        return 200, chunks

    return handle
