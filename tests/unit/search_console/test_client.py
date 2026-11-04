"""HTTP behaviour of the Search Console client against recorded responses."""

from __future__ import annotations

import json
from datetime import date

import pytest
import requests
import responses

from omw.sources.search_console.client import (
    MAX_ROW_LIMIT,
    SearchConsoleAccessError,
    SearchConsoleClient,
    default_session,
)
from tests.unit.search_console.conftest import FIXTURES, SITE

URL = SearchConsoleClient.query_url(SITE)


def _client(row_limit: int = MAX_ROW_LIMIT) -> SearchConsoleClient:
    session = default_session(max_attempts=3, backoff_factor=0.0, timeout=5)
    return SearchConsoleClient(lambda: "test-token", session=session, row_limit=row_limit)


def _fixture(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


def test_url_encodes_the_property() -> None:
    assert URL.endswith("/sites/sc-domain%3Aexample.test/searchAnalytics/query")
    assert SearchConsoleClient.query_url("https://example.test/").endswith(
        "/sites/https%3A%2F%2Fexample.test%2F/searchAnalytics/query"
    )


@responses.activate
def test_paginates_with_start_row_until_a_short_page() -> None:
    responses.post(URL, body=_fixture("page_1"), content_type="application/json")
    responses.post(URL, body=_fixture("page_2"), content_type="application/json")
    rows = list(_client(row_limit=2).query(SITE, date(2026, 10, 1), date(2026, 10, 1), ["date", "query"]))
    assert [row["keys"][1] for row in rows] == ["a", "b", "c"]
    bodies = [json.loads(call.request.body or "") for call in responses.calls]
    assert [body["startRow"] for body in bodies] == [0, 2]
    assert bodies[0] == {
        "startDate": "2026-10-01",
        "endDate": "2026-10-01",
        "dimensions": ["date", "query"],
        "searchType": "web",
        "dataState": "all",
        "rowLimit": 2,
        "startRow": 0,
    }
    assert responses.calls[0].request.headers["Authorization"] == "Bearer test-token"


@responses.activate
def test_empty_page_ends_the_report() -> None:
    responses.post(URL, body=_fixture("empty"), content_type="application/json")
    rows = list(_client().query(SITE, date(2026, 10, 1), date(2026, 10, 3), ["date"]))
    assert rows == []
    assert len(responses.calls) == 1


@responses.activate
def test_rate_limit_is_retried_then_succeeds() -> None:
    responses.post(URL, body=_fixture("rate_limited"), status=429, content_type="application/json")
    responses.post(URL, body=_fixture("page_2"), content_type="application/json")
    rows = list(_client().query(SITE, date(2026, 10, 1), date(2026, 10, 1), ["date", "query"]))
    assert len(rows) == 1
    trace = [
        (
            getattr(c.response, "status_code", str(c.response)[:400]),
            json.loads(c.request.body or "")["startRow"],
        )
        for c in responses.calls
    ]
    assert trace == [(429, 0), (200, 0)]


@responses.activate
def test_rate_limit_gives_up_after_max_attempts() -> None:
    for _ in range(3):
        responses.post(URL, body=_fixture("rate_limited"), status=429, content_type="application/json")
    with pytest.raises(requests.HTTPError):
        list(_client().query(SITE, date(2026, 10, 1), date(2026, 10, 1), ["date"]))
    assert len(responses.calls) == 3


@responses.activate
def test_forbidden_is_not_retried_and_names_the_property() -> None:
    responses.post(URL, body=_fixture("forbidden"), status=403, content_type="application/json")
    with pytest.raises(SearchConsoleAccessError, match="sc-domain:example.test"):
        list(_client().query(SITE, date(2026, 10, 1), date(2026, 10, 1), ["date"]))
    assert len(responses.calls) == 1


def test_row_limit_is_bounded() -> None:
    with pytest.raises(ValueError):
        SearchConsoleClient(lambda: "t", session=requests.Session(), row_limit=MAX_ROW_LIMIT + 1)
