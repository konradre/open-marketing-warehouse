"""Thin client for the GA4 Data API ``runReport`` method, paging with ``limit`` and ``offset``."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from datetime import date
from typing import Any

import requests
from dlt.sources.helpers.requests import Client as RetryingClient

log = logging.getLogger(__name__)

API_BASE = "https://analyticsdata.googleapis.com/v1beta"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
MAX_LIMIT = 250_000
"""The API's hard maximum for ``limit`` per request."""

TokenProvider = Callable[[], str]


class GA4AccessError(PermissionError):
    """The API refused the credentials for a property (HTTP 401/403). Not retried."""


def service_account_token_provider(credentials_path: str) -> TokenProvider:
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
    max_attempts: int = 5, backoff_factor: float = 1.0, timeout: float = 120.0
) -> requests.Session:
    return RetryingClient(
        request_timeout=timeout,
        request_max_attempts=max_attempts,
        request_backoff_factor=backoff_factor,
        raise_for_status=False,
    ).session


class GA4Client:
    def __init__(
        self,
        token_provider: TokenProvider,
        session: requests.Session | None = None,
        limit: int = MAX_LIMIT,
        timeout: float = 120.0,
    ) -> None:
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}, got {limit}")
        self._token = token_provider
        self._session = session or default_session(timeout=timeout)
        self._limit = limit
        self._timeout = timeout

    @staticmethod
    def report_url(property_id: str) -> str:
        return f"{API_BASE}/properties/{property_id.removeprefix('properties/')}:runReport"

    def run_report(
        self,
        property_id: str,
        start: date,
        end: date,
        dimensions: Sequence[str],
        metrics: Sequence[str],
    ) -> Iterator[dict[str, Any]]:
        """Every row of the report (``dimensionValues`` / ``metricValues``), across all pages."""
        offset = 0
        while True:
            body = {
                "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
                "dimensions": [{"name": name} for name in dimensions],
                "metrics": [{"name": name} for name in metrics],
                "limit": self._limit,
                "offset": offset,
                "keepEmptyRows": False,
                "returnPropertyQuota": True,
            }
            payload = self._post(property_id, body)
            rows = payload.get("rows", [])
            yield from rows
            row_count = int(payload.get("rowCount", 0))
            offset += len(rows)
            if not rows or offset >= row_count:
                return

    def _post(self, property_id: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._session.post(
            self.report_url(property_id),
            json=body,
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=self._timeout,
        )
        if response.status_code in (401, 403):
            raise GA4AccessError(
                f"GA4 refused access to property {property_id!r} (HTTP {response.status_code}): add the "
                "service account's email as a Viewer on the property in Admin > Property access management. "
                f"Response: {response.text[:300]}"
            )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        quota = payload.get("propertyQuota", {}).get("tokensPerDay", {})
        if quota:
            log.debug("ga4 property %s: %s daily tokens remaining", property_id, quota.get("remaining"))
        return payload
