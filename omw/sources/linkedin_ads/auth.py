"""OAuth refresh-token flow and the request auth that carries the bearer token and the version headers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import requests
from requests import PreparedRequest
from requests.auth import AuthBase

TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
RESTLI_PROTOCOL = "2.0.0"


def refresh_access_token(
    client_id: str, client_secret: str, refresh_token: str, session: requests.Session | None = None
) -> tuple[str, float]:
    """A fresh access token and its expiry (epoch seconds) from a refresh token."""
    response = (session or requests.Session()).post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    if response.status_code in (400, 401):
        raise PermissionError(
            f"LinkedIn refused the refresh token (HTTP {response.status_code}): re-run the OAuth consent "
            f"for an app approved for the Advertising API. {response.text[:300]}"
        )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return str(payload["access_token"]), time.time() + float(payload.get("expires_in", 3600))


class LinkedInAuth(AuthBase):
    """Bearer auth plus the two headers every versioned Marketing API call needs.

    A plain ``requests`` auth, not a dlt config spec: dlt would otherwise try to build it from config
    when it is passed to the source.
    """

    def __init__(
        self,
        version: str,
        token_provider: Callable[[], str] | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.version = version
        self._provider = token_provider
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._session = session
        self._token: str | None = None
        self._expires_at = 0.0

    def token(self) -> str:
        if self._provider is not None:
            return self._provider()
        if self._token is None or time.time() >= self._expires_at - 60:
            if not (self._client_id and self._client_secret and self._refresh_token):
                raise ValueError("linkedin_ads needs client_id, client_secret and refresh_token")
            self._token, self._expires_at = refresh_access_token(
                self._client_id, self._client_secret, self._refresh_token, self._session
            )
        return self._token

    def __call__(self, request: PreparedRequest) -> PreparedRequest:
        request.headers["Authorization"] = f"Bearer {self.token()}"
        request.headers["LinkedIn-Version"] = self.version
        request.headers["X-Restli-Protocol-Version"] = RESTLI_PROTOCOL
        return request
