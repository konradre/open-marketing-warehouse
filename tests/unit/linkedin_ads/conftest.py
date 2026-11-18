"""Recorded LinkedIn Marketing API responses, registered on a `responses` mock per endpoint."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import responses

from omw.sources.linkedin_ads import LinkedInAuth
from omw.sources.linkedin_ads.source import API_BASE, default_session

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "linkedin_ads"
ACCOUNT = "512345678"
VERSION = "202610"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def body(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


def _creative_pivot(request: Any) -> tuple[bool, str]:
    return "pivot=CREATIVE" in str(request.url), "not the creative pivot"


def _campaign_pivot(request: Any) -> tuple[bool, str]:
    return "pivot=CREATIVE" not in str(request.url), "the creative pivot"


_DAY = r"\(year:(\d+),month:(\d+),day:(\d+)\)"
_RANGE = re.compile(rf"dateRange=\(start:{_DAY},end:{_DAY}\)")


def _capped(name: str, cap: int) -> Any:
    """adAnalytics as LinkedIn documents it: the fixture rows inside the requested dateRange, cut off at
    ``cap`` elements with nothing in the response to say so."""

    def answer(request: Any) -> tuple[int, dict[str, str], str]:
        found = _RANGE.search(str(request.url))
        assert found, request.url
        first, last = tuple(int(p) for p in found.groups()[:3]), tuple(int(p) for p in found.groups()[3:])
        elements = [
            e
            for e in fixture(name)["elements"]
            if first <= tuple(e["dateRange"]["start"][k] for k in ("year", "month", "day")) <= last
        ][:cap]
        paging = {"start": 0, "count": len(elements), "links": []}
        return 200, {"Content-Type": "application/json"}, json.dumps({"elements": elements, "paging": paging})

    return answer


def register_api(mock: Any, analytics: str = "ad_analytics", cap: int | None = None) -> None:
    """The whole API surface the source touches, served from fixtures. The creative pivot is registered
    before the catch-all campaign analytics mock. With ``cap``, adAnalytics answers by date range and
    cuts every response off at that many elements."""
    analytics_url = re.compile(re.escape(f"{API_BASE}adAnalytics") + r".*")
    # Exactly one registration matches each request: `responses` drops the first of several that match,
    # and a split window asks the same pivot more than once.
    if cap is not None:
        creatives = _capped("ad_analytics_creatives", cap)
        mock.add_callback(responses.GET, analytics_url, callback=creatives, match=[_creative_pivot])
    else:
        mock.add(
            responses.GET,
            analytics_url,
            body=body("ad_analytics_creatives"),
            content_type="application/json",
            match=[_creative_pivot],
        )
    mock.get(f"{API_BASE}adAccounts/{ACCOUNT}", body=body("ad_account"), content_type="application/json")
    mock.get(f"{API_BASE}adAccounts", body=body("ad_accounts"), content_type="application/json")
    mock.get(
        f"{API_BASE}adAccounts/{ACCOUNT}/adCampaignGroups",
        body=body("campaign_groups"),
        content_type="application/json",
    )
    mock.add(
        responses.GET,
        f"{API_BASE}adAccounts/{ACCOUNT}/adCampaigns",
        body=body("campaigns_page_1"),
        content_type="application/json",
        match=[
            responses.matchers.query_param_matcher({"q": "search", "pageSize": "100"}, strict_match=False)
        ],
    )
    mock.add(
        responses.GET,
        f"{API_BASE}adAccounts/{ACCOUNT}/adCampaigns",
        body=body("campaigns_page_2"),
        content_type="application/json",
        match=[responses.matchers.query_param_matcher({"pageToken": "page2token"}, strict_match=False)],
    )
    mock.get(
        f"{API_BASE}adAccounts/{ACCOUNT}/creatives", body=body("creatives"), content_type="application/json"
    )
    mock.add(
        responses.GET,
        re.compile(re.escape(f"{API_BASE}leadFormResponses") + r".*"),
        body=body("lead_form_responses"),
        content_type="application/json",
    )
    if cap is not None:
        campaigns = _capped(analytics, cap)
        mock.add_callback(responses.GET, analytics_url, callback=campaigns, match=[_campaign_pivot])
    else:
        mock.add(
            responses.GET,
            analytics_url,
            body=body(analytics),
            content_type="application/json",
            match=[_campaign_pivot],
        )


@pytest.fixture
def auth() -> LinkedInAuth:
    return LinkedInAuth(VERSION, token_provider=lambda: "test-token")


def fast_session(auth: LinkedInAuth) -> Any:
    """A session with no backoff; build it inside the test, after `responses` is active."""
    return default_session(auth, max_attempts=3, backoff_factor=0.0)
