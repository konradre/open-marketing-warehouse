"""REST client for ``GoogleAdsService.SearchStream``.

One POST per query; the body is a JSON array of chunks, each with ``results``. A stream that fails midway
(a chunk carrying ``error`` after some results) is a failed query: nothing from it is returned, so a slice
is replaced only with a complete answer. 429 and 5xx are retried with backoff; 4xx carry Google's
``GoogleAdsFailure`` details into ``GoogleAdsQueryError``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import requests
from dlt.sources.helpers.requests import Client as RetryingClient

log = logging.getLogger(__name__)

API_VERSION = "v22"
"""Google Ads API version in the URL. Versions live about a year; bump this constant (or configure
``api_version``) when Google sunsets it."""

API_BASE = "https://googleads.googleapis.com"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/adwords"

TokenProvider = Callable[[], str]


class GoogleAdsQueryError(RuntimeError):
    """A query the API refused, with the ``GoogleAdsFailure`` errors Google returned."""

    def __init__(self, customer_id: str, status: int, message: str, errors: list[dict[str, Any]]) -> None:
        codes = [next(iter(e.get("errorCode", {}).items()), ("unknown", "?")) for e in errors]
        summary = ", ".join(f"{kind}={code}" for kind, code in codes) or "no error details"
        super().__init__(
            f"Google Ads refused the query for customer {customer_id} (HTTP {status}): {message} [{summary}]"
        )
        self.customer_id = customer_id
        self.status = status
        self.errors = errors


def oauth_token_provider(client_id: str, client_secret: str, refresh_token: str) -> TokenProvider:
    """Access tokens from an OAuth refresh token, refreshed when they expire."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credentials = Credentials(  # type: ignore[no-untyped-call]
        None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=[SCOPE],
    )

    def token() -> str:
        if not credentials.valid:
            credentials.refresh(Request())  # type: ignore[no-untyped-call]
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


def _failure_errors(payload: Any) -> list[dict[str, Any]]:
    """The ``errors`` list from a ``GoogleAdsFailure`` detail, if the error payload carries one."""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return []
    for detail in error.get("details", []):
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("GoogleAdsFailure"):
            found: list[dict[str, Any]] = detail.get("errors", [])
            return found
    return []


def _error_message(payload: Any) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message", ""))
    return str(payload)[:300]


class GoogleAdsClient:
    def __init__(
        self,
        token_provider: TokenProvider,
        developer_token: str,
        login_customer_id: str | None = None,
        session: requests.Session | None = None,
        api_version: str = API_VERSION,
        timeout: float = 120.0,
    ) -> None:
        self._token = token_provider
        self._developer_token = developer_token
        self._login_customer_id = login_customer_id.replace("-", "") if login_customer_id else None
        self._session = session or default_session(timeout=timeout)
        self._api_version = api_version
        self._timeout = timeout

    def search_stream_url(self, customer_id: str) -> str:
        return (
            f"{API_BASE}/{self._api_version}/customers/{customer_id.replace('-', '')}/googleAds:searchStream"
        )

    def headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token()}", "developer-token": self._developer_token}
        if self._login_customer_id:
            headers["login-customer-id"] = self._login_customer_id
        return headers

    def search_stream(self, customer_id: str, query: str) -> list[dict[str, Any]]:
        """Every result row of ``query`` for ``customer_id``; a failed stream returns nothing and raises."""
        response = self._session.post(
            self.search_stream_url(customer_id),
            json={"query": query},
            headers=self.headers(),
            timeout=self._timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            response.raise_for_status()
            raise GoogleAdsQueryError(customer_id, response.status_code, "response is not JSON", []) from None
        if response.status_code >= 400:
            raise GoogleAdsQueryError(
                customer_id, response.status_code, _error_message(payload), _failure_errors(payload)
            )
        if isinstance(payload, dict):
            payload = [payload]
        rows: list[dict[str, Any]] = []
        for chunk in payload:
            if not isinstance(chunk, dict):
                continue
            if "error" in chunk:
                raise GoogleAdsQueryError(
                    customer_id,
                    int(chunk["error"].get("code", 400)),
                    _error_message(chunk),
                    _failure_errors(chunk),
                )
            rows.extend(chunk.get("results", []))
        log.debug("google ads customer %s: %s rows in %s chunks", customer_id, len(rows), len(payload))
        return rows
