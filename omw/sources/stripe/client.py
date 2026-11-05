"""Thin client for Stripe's list endpoints: ``has_more`` / ``starting_after`` paging, bounded retries."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import requests
from dlt.sources.helpers.requests import Client as RetryingClient

log = logging.getLogger(__name__)

API_BASE = "https://api.stripe.com/v1"
API_VERSION = "2026-01-28"
"""Pinned ``Stripe-Version`` so response shapes do not change under the pipeline when the account's
default API version is upgraded."""
PAGE_LIMIT = 100


class StripeAccessError(PermissionError):
    """Stripe refused the key (HTTP 401/403). Not retried."""


def default_session(
    max_attempts: int = 5, backoff_factor: float = 1.0, timeout: float = 60.0
) -> requests.Session:
    return RetryingClient(
        request_timeout=timeout,
        request_max_attempts=max_attempts,
        request_backoff_factor=backoff_factor,
        raise_for_status=False,
    ).session


class StripeClient:
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        page_limit: int = PAGE_LIMIT,
        api_version: str = API_VERSION,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._session = session or default_session(timeout=timeout)
        self._page_limit = page_limit
        self._api_version = api_version
        self._timeout = timeout

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Stripe-Version": self._api_version}

    def list(self, resource: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Every object of a list endpoint (``customers``, ``invoices``, ...), oldest page order as Stripe
        returns it (newest first), following ``starting_after`` until ``has_more`` is false."""
        query: dict[str, Any] = {"limit": self._page_limit, **(params or {})}
        while True:
            page = self._get(f"{API_BASE}/{resource}", query)
            data = page.get("data", [])
            yield from data
            if not page.get("has_more") or not data:
                return
            query["starting_after"] = data[-1]["id"]

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self._session.get(url, params=params, headers=self.headers(), timeout=self._timeout)
        if response.status_code in (401, 403):
            raise StripeAccessError(
                f"Stripe refused the API key (HTTP {response.status_code}): use a restricted key with read "
                "access to customers, subscriptions, invoices and balance transactions. "
                f"{response.text[:300]}"
            )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        log.debug(
            "stripe %s: %s objects, has_more=%s",
            url.rsplit("/", 1)[-1],
            len(payload.get("data", [])),
            payload.get("has_more"),
        )
        return payload
