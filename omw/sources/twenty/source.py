"""The dlt source: three declarative ``rest_api`` resources, cursor-paged, incremental on ``updatedAt``."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import dlt
import requests
from dlt.destinations.adapters import clickhouse_adapter
from dlt.extract import DltResource
from dlt.sources.helpers.requests import Client as RetryingClient
from dlt.sources.helpers.rest_client.auth import BearerTokenAuth
from dlt.sources.helpers.rest_client.paginators import JSONResponseCursorPaginator
from dlt.sources.rest_api import rest_api_resources
from dlt.sources.rest_api.typing import RESTAPIConfig

from omw.sources.columns import column
from omw.sources.twenty.shapes import (
    COMPANY_COLUMNS,
    OPPORTUNITY_COLUMNS,
    PERSON_COLUMNS,
    shape_company,
    shape_opportunity,
    shape_person,
)

SOURCE_NAME = "twenty"
PAGE_SIZE = 60
"""Twenty's maximum page size."""
INITIAL_UPDATED_AT = datetime(1970, 1, 1, tzinfo=UTC)

OBJECTS: dict[str, tuple[str, Callable[[Mapping[str, Any]], dict[str, Any]], dict[str, Any]]] = {
    # resource name -> (REST path and data key, row shaper, column hints)
    "companies": ("companies", shape_company, COMPANY_COLUMNS),
    "people": ("people", shape_person, PERSON_COLUMNS),
    "opportunities": ("opportunities", shape_opportunity, OPPORTUNITY_COLUMNS),
}


def updated_at_filter(value: datetime | str) -> str:
    """Twenty's filter syntax: one parameter carrying the operator, ``updatedAt[gte]:"<iso>"``.

    The cursor is the shaped ``updated_at`` datetime (the map step runs before the incremental), so it is
    formatted back to the API's UTC ISO form here.
    """
    if isinstance(value, datetime):
        value = value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f'updatedAt[gte]:"{value}"'


def paginator() -> JSONResponseCursorPaginator:
    return JSONResponseCursorPaginator(
        cursor_path="pageInfo.endCursor", cursor_param="starting_after", has_more_path="pageInfo.hasNextPage"
    )


def default_session(max_attempts: int = 5, backoff_factor: float = 1.0) -> requests.Session:
    return RetryingClient(
        request_timeout=60,
        request_max_attempts=max_attempts,
        request_backoff_factor=backoff_factor,
        raise_for_status=False,
    ).session


def rest_base_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/rest/"


def object_resources(base_url: str, api_key: str, session: requests.Session | None) -> list[DltResource]:
    config: RESTAPIConfig = {
        "client": {
            "base_url": rest_base_url(base_url),
            "auth": BearerTokenAuth(api_key),
            "paginator": paginator(),
            **({"session": session} if session is not None else {}),
        },
        "resource_defaults": {
            "write_disposition": "merge",
            "primary_key": "id",
            "endpoint": {"params": {"limit": PAGE_SIZE, "order_by": "updatedAt[AscNullsLast]"}},
        },
        "resources": [
            {
                "name": name,
                "endpoint": {
                    "path": path,
                    "data_selector": f"data.{path}",
                    "incremental": {
                        "cursor_path": "updated_at",
                        "initial_value": INITIAL_UPDATED_AT,
                        "start_param": "filter",
                        "convert": updated_at_filter,
                    },
                },
                "columns": columns,
                "processing_steps": [{"map": shaper}],
            }
            for name, (path, shaper, columns) in OBJECTS.items()
        ],
    }
    return [clickhouse_adapter(resource, sort=["id"]) for resource in rest_api_resources(config)]


def sync_windows_resource() -> DltResource:
    def rows() -> Iterator[dict[str, Any]]:
        yield {"mode": "incremental", "loaded_at": datetime.now(tz=UTC)}

    return dlt.resource(
        rows,
        name="sync_windows",
        write_disposition="append",
        columns={"mode": column("text"), "loaded_at": column("timestamp")},
    )


@dlt.source(name=SOURCE_NAME, max_table_nesting=0)
def twenty(
    base_url: str = dlt.config.value,
    api_key: str | None = dlt.secrets.value,
    session: requests.Session | None = None,
) -> Iterator[DltResource]:
    """Companies, people and opportunities from a self-hosted Twenty, changed since the last run.

    ``base_url`` is the Twenty instance (``https://twenty.example.com``); ``api_key`` an API key from
    Settings > Developers. ``session`` is injectable for tests.
    """
    if not base_url:
        raise ValueError("twenty needs base_url (sources.twenty.base_url)")
    if not api_key:
        raise ValueError("twenty needs api_key (sources.twenty.api_key)")
    yield from object_resources(base_url, api_key, session)
    yield sync_windows_resource()
