"""Fixture mode: a ``requests`` transport that answers from fixture files instead of the network.

The session it is mounted on goes to the real clients (``SearchConsoleClient``, ``GA4Client``,
``GoogleAdsClient``, ``StripeClient``, dlt's ``RESTClient`` for LinkedIn and Twenty), so their request
building, paging and parsing all run as in production. Only the socket is replaced: every ``http://`` and
``https://`` request of the session ends here, and a request no handler knows gets a 404, never a real
call.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import requests
from dlt.sources.helpers.requests import Client as RetryingClient
from requests.adapters import BaseAdapter

TOKEN = "fixture-token"  # noqa: S105 - not a credential: fixture mode never authenticates


def token_provider() -> str:
    return TOKEN


@dataclass(frozen=True)
class Call:
    """One request as a handler sees it."""

    method: str
    host: str
    path: str
    query: Mapping[str, str]
    """Query parameters, the first value of each."""
    raw_query: str
    """The query string with percent-escapes decoded, for Rest.li expressions."""
    body: Any
    """The JSON body, or ``None``."""


Answer = tuple[int, Any]
Handler = Callable[[Call], Answer]


def not_found(call: Call) -> Answer:
    message = f"fixture mode has no answer for {call.method} {call.host}{call.path}"
    return 404, {"error": {"code": 404, "message": message}}


class Fixtures:
    """The fixture files of one snapshot directory (``<out>/initial`` or ``<out>/restated``), read once."""

    def __init__(self, directory: Path) -> None:
        if not directory.is_dir():
            raise FileNotFoundError(f"no fixture snapshot at {directory}: run `omw demo generate` first")
        self.directory = directory
        self._load = cache(self._read)

    def _read(self, source: str, name: str) -> Any:
        return json.loads((self.directory / source / f"{name}.json").read_text())

    def load(self, source: str, name: str) -> Any:
        return self._load(source, name)

    def exists(self, source: str, name: str) -> bool:
        return (self.directory / source / f"{name}.json").exists()


@dataclass
class FixtureAdapter(BaseAdapter):
    """Routes each request to the handler registered for its host."""

    routes: dict[str, Handler]
    calls: list[Call] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__init__()

    def send(  # type: ignore[override]
        self, request: requests.PreparedRequest, **_: Any
    ) -> requests.Response:
        parts = urlsplit(request.url or "")
        body: Any = None
        if request.body:
            raw = request.body.decode() if isinstance(request.body, bytes) else str(request.body)
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
        call = Call(
            method=(request.method or "GET").upper(),
            host=parts.netloc,
            path=unquote(parts.path),
            query={key: values[0] for key, values in parse_qs(parts.query).items()},
            raw_query=unquote(parts.query),
            body=body,
        )
        self.calls.append(call)
        status, payload = self.routes.get(call.host, not_found)(call)
        response = requests.Response()
        response.status_code = status
        response.reason = "OK" if status < 400 else "Fixture error"
        response.url = request.url or ""
        response.request = request
        response.encoding = "utf-8"
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(payload).encode()
        return response

    def close(self) -> None:
        return None


def fixture_session(adapter: FixtureAdapter) -> requests.Session:
    """A session like the clients' own (dlt's retrying session), with every request sent to ``adapter``."""
    session: requests.Session = RetryingClient(
        request_timeout=30, request_max_attempts=1, raise_for_status=False
    ).session
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
