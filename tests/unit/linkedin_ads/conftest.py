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


def register_api(mock: Any, analytics: str = "ad_analytics") -> None:
    """The whole API surface the source touches, served from fixtures. The creative pivot is registered
    before the catch-all campaign analytics mock."""
    mock.add(
        responses.GET,
        re.compile(re.escape(f"{API_BASE}adAnalytics") + r".*"),
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
        re.compile(re.escape(f"{API_BASE}adAnalytics") + r".*"),
        body=body(analytics),
        content_type="application/json",
    )


@pytest.fixture
def auth() -> LinkedInAuth:
    return LinkedInAuth(VERSION, token_provider=lambda: "test-token")


def fast_session(auth: LinkedInAuth) -> Any:
    """A session with no backoff; build it inside the test, after `responses` is active."""
    return default_session(auth, max_attempts=3, backoff_factor=0.0)
