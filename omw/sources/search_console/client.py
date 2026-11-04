"""Thin client for the Search Console Search Analytics API.

Pages with ``startRow`` / ``rowLimit`` until a page comes back short. Retries 429 and 5xx with backoff
through dlt's requests helper; never retries an auth error (401/403), which means the service account is not
a user on the property (see README, "Search Console access").
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from datetime import date
from typing import Any
from urllib.parse import quote

import requests
from dlt.sources.helpers.requests import Client as RetryingClient

log = logging.getLogger(__name__)

API_BASE = "https://searchconsole.googleapis.com/webmasters/v3"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
MAX_ROW_LIMIT = 25_000
"""The API's hard maximum for ``rowLimit``."""

TokenProvider = Callable[[], str]


class SearchConsoleAccessError(PermissionError):
    """The API refused the credentials for a property (HTTP 401/403). Not retried."""


def service_account_token_provider(credentials_path: str) -> TokenProvider:
    """Access tokens for a service account JSON file, refreshed when they expire."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        credentials_path, scopes=[SCOPE]
    )

    def token() -> str:
        if not credentials.valid:
            credentials.refresh(Request())
        return str(credentials.token)

    return token


def default_session(
    max_attempts: int = 5, backoff_factor: float = 1.0, timeout: float = 60.0
) -> requests.Session:
    """A session that retries 429/5xx with exponential backoff and raises on other errors."""
    return RetryingClient(
        request_timeout=timeout,
        request_max_attempts=max_attempts,
        request_backoff_factor=backoff_factor,
        raise_for_status=False,
    ).session


class SearchConsoleClient:
    def __init__(
        self,
        token_provider: TokenProvider,
        session: requests.Session | None = None,
        row_limit: int = MAX_ROW_LIMIT,
        timeout: float = 60.0,
    ) -> None:
        if not 1 <= row_limit <= MAX_ROW_LIMIT:
            raise ValueError(f"row_limit must be between 1 and {MAX_ROW_LIMIT}, got {row_limit}")
        self._token = token_provider
        self._session = session or default_session(timeout=timeout)
        self._row_limit = row_limit
        self._timeout = timeout

    @staticmethod
    def query_url(site_url: str) -> str:
        return f"{API_BASE}/sites/{quote(site_url, safe='')}/searchAnalytics/query"

    def query(
        self,
        site_url: str,
        start: date,
        end: date,
        dimensions: Sequence[str],
        search_type: str = "web",
        data_state: str = "all",
    ) -> Iterator[dict[str, Any]]:
        """Every row of the report for ``[start, end]`` (inclusive), across all pages."""
        start_row = 0
        while True:
            body = {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": list(dimensions),
                "searchType": search_type,
                "dataState": data_state,
                "rowLimit": self._row_limit,
                "startRow": start_row,
            }
            rows = self._post(site_url, body)
            yield from rows
            if len(rows) < self._row_limit:
                return
            start_row += len(rows)

    def _post(self, site_url: str, body: dict[str, Any]) -> list[dict[str, Any]]:
        response = self._session.post(
            self.query_url(site_url),
            json=body,
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=self._timeout,
        )
        if response.status_code in (401, 403):
            raise SearchConsoleAccessError(
                f"Search Console refused access to {site_url!r} (HTTP {response.status_code}): "
                "add the service account's email as a user on the property in Search Console > Settings > "
                f"Users and permissions. Response: {response.text[:300]}"
            )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("rows", [])
        log.debug(
            "search console %s %s..%s startRow=%s rows=%s",
            site_url,
            body["startDate"],
            body["endDate"],
            body["startRow"],
            len(rows),
        )
        return list(rows)
