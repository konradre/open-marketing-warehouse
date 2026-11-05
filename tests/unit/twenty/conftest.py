"""Recorded Twenty REST responses, served by a `responses` mock."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import responses

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "twenty"
BASE_URL = "https://twenty.example.test"
REST = f"{BASE_URL}/rest/"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def body(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


def _deleted_pass(request: Any) -> tuple[bool, str]:
    """Matches the deleted-records pass: the filter names deletedAt."""
    query = parse_qs(urlparse(str(request.url)).query)
    return "deletedAt" in query.get("filter", [""])[0], "not the deleted pass"


def _empty(path: str) -> str:
    return json.dumps(
        {"data": {path: []}, "pageInfo": {"hasNextPage": False, "startCursor": None, "endCursor": None}}
    )


def register_api(
    mock: Any, opportunities: str = "opportunities", deleted_opportunities: str | None = None
) -> None:
    # The deleted pass first: its requests also carry limit=60 and would match the live mocks below.
    for path in ("companies", "people", "opportunities"):
        deleted = deleted_opportunities if path == "opportunities" and deleted_opportunities else None
        mock.add(
            responses.GET,
            f"{REST}{path}",
            body=body(deleted) if deleted else _empty(path),
            content_type="application/json",
            match=[_deleted_pass],
        )
    mock.add(
        responses.GET,
        f"{REST}companies",
        body=body("companies_page_1"),
        content_type="application/json",
        match=[responses.matchers.query_param_matcher({"limit": "60"}, strict_match=False)],
    )
    mock.add(
        responses.GET,
        f"{REST}companies",
        body=body("companies_page_2"),
        content_type="application/json",
        match=[
            responses.matchers.query_param_matcher(
                {"starting_after": "cursor-companies-2"}, strict_match=False
            )
        ],
    )
    mock.add(
        responses.GET,
        re.compile(re.escape(f"{REST}people") + r".*"),
        body=body("people"),
        content_type="application/json",
    )
    mock.add(
        responses.GET,
        re.compile(re.escape(f"{REST}opportunities") + r".*"),
        body=body(opportunities),
        content_type="application/json",
    )
