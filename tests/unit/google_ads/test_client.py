"""The searchStream client against recorded responses: chunks, partial failure, errors, retries."""

from __future__ import annotations

import json

import pytest
import requests
import responses

from omw.sources.google_ads.client import GoogleAdsClient, GoogleAdsQueryError, default_session
from tests.unit.google_ads.conftest import CUSTOMER, FIXTURES

QUERY = "SELECT campaign.id FROM campaign"


def _client() -> GoogleAdsClient:
    session = default_session(max_attempts=3, backoff_factor=0.0, timeout=5)
    return GoogleAdsClient(
        lambda: "test-token", "dev-token", "111-222-3333", session=session, api_version="v22"
    )


def _body(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


URL = _client().search_stream_url(CUSTOMER)


def test_url_and_headers() -> None:
    assert URL == "https://googleads.googleapis.com/v22/customers/1234567890/googleAds:searchStream"
    assert _client().search_stream_url("123-456-7890") == URL
    headers = _client().headers()
    assert headers == {
        "Authorization": "Bearer test-token",
        "developer-token": "dev-token",
        "login-customer-id": "1112223333",
    }


@responses.activate
def test_all_chunks_of_a_stream_are_joined() -> None:
    responses.post(URL, body=_body("campaigns_stream"), content_type="application/json")
    rows = _client().search_stream(CUSTOMER, QUERY)
    assert len(rows) == 9
    assert {r["campaign"]["id"] for r in rows} == {"20010001", "20010002", "20010003"}
    assert json.loads(responses.calls[0].request.body or "") == {"query": QUERY}


@responses.activate
def test_an_empty_stream_is_no_rows() -> None:
    responses.post(URL, body=_body("empty_stream"), content_type="application/json")
    assert _client().search_stream(CUSTOMER, QUERY) == []


@responses.activate
def test_a_stream_that_fails_midway_returns_nothing_and_names_the_error() -> None:
    responses.post(URL, body=_body("partial_failure"), content_type="application/json")
    with pytest.raises(GoogleAdsQueryError) as info:
        _client().search_stream(CUSTOMER, QUERY)
    assert "quotaError=RESOURCE_TEMPORARILY_EXHAUSTED" in str(info.value)
    assert info.value.status == 429 and info.value.customer_id == CUSTOMER
    assert len(responses.calls) == 1  # the HTTP status was 200: nothing to retry, the stream itself failed


@responses.activate
def test_a_refused_customer_carries_googles_error_code() -> None:
    responses.post(URL, body=_body("error_invalid_customer"), status=400, content_type="application/json")
    with pytest.raises(GoogleAdsQueryError, match="authorizationError=CUSTOMER_NOT_ENABLED"):
        _client().search_stream(CUSTOMER, QUERY)
    assert len(responses.calls) == 1


@responses.activate
def test_rate_limit_is_retried_then_succeeds() -> None:
    responses.post(URL, body=_body("error_rate_limited"), status=429, content_type="application/json")
    responses.post(URL, body=_body("empty_stream"), content_type="application/json")
    assert _client().search_stream(CUSTOMER, QUERY) == []
    assert len(responses.calls) == 2


@responses.activate
def test_rate_limit_gives_up_after_max_attempts() -> None:
    for _ in range(3):
        responses.post(URL, body=_body("error_rate_limited"), status=429, content_type="application/json")
    with pytest.raises(GoogleAdsQueryError, match="HTTP 429"):
        _client().search_stream(CUSTOMER, QUERY)
    assert len(responses.calls) == 3


@responses.activate
def test_non_json_error_surfaces_the_http_error() -> None:
    responses.post(URL, body="<html>bad gateway</html>", status=502, content_type="text/html")
    responses.post(URL, body="<html>bad gateway</html>", status=502, content_type="text/html")
    responses.post(URL, body="<html>bad gateway</html>", status=502, content_type="text/html")
    with pytest.raises(requests.HTTPError):
        _client().search_stream(CUSTOMER, QUERY)
