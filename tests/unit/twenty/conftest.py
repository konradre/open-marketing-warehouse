"""Recorded Twenty REST responses, served by a `responses` mock."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import responses

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "twenty"
BASE_URL = "https://twenty.example.test"
REST = f"{BASE_URL}/rest/"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def body(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


def register_api(mock: Any, opportunities: str = "opportunities") -> None:
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
