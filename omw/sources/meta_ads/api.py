"""Connecting the Meta SDK with a paced session."""

from __future__ import annotations

from typing import Any

import requests
from dlt.sources.helpers.requests import Client as RetryingClient
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.api import FacebookAdsApi

from omw.pacing import MetaPacer

TRANSIENT_ERROR_CODES = frozenset({1, 2, 341})
"""Graph API codes Meta documents as "try again": unknown error, service unavailable, temporary block.
Retried a few times with backoff. Rate-limit codes are not here: the pacer stops on them."""


def transient_error(response: requests.Response | None, exception: BaseException | None) -> bool:
    if response is None:
        return False
    try:
        code = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return False
    return isinstance(code, int) and code in TRANSIENT_ERROR_CODES


def paced_session(
    pacer: MetaPacer, access_token: str, timeout: float = 300.0, max_attempts: int = 5
) -> requests.Session:
    """A requests session that retries 5xx/429 and Meta's transient codes a bounded number of times, with
    the pacer wrapped around every send."""
    session = RetryingClient(
        request_timeout=timeout,
        raise_for_status=False,
        retry_condition=transient_error,
        request_max_attempts=max_attempts,
        request_backoff_factor=2,
    ).session
    session.params = {"access_token": access_token}
    return pacer.install(session)


def connect(
    account_id: str,
    access_token: str,
    pacer: MetaPacer,
    api_version: str | None = None,
    timeout: float = 300.0,
) -> Any:
    """An ``AdAccount`` whose API calls go through the paced session."""
    api = FacebookAdsApi.init(access_token=access_token, api_version=api_version, timeout=timeout)
    api._session.requests = paced_session(pacer, access_token, timeout)
    return AdAccount(f"act_{account_id.removeprefix('act_')}", api=api)
