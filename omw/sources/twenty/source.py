"""The dlt source: three declarative ``rest_api`` resources, cursor-paged, incremental on ``updatedAt``, plus
one deleted-records pass per object (``deletedAt[is]:NOT_NULL``, incremental on ``deletedAt``) that merges the
soft-deleted rows into the same tables so the marts can exclude them."""

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
from dlt.sources.rest_api.typing import Endpoint, EndpointResource, RESTAPIConfig

from omw.sources.columns import column
from omw.sources.twenty.activity import (
    NOTE_COLUMNS,
    NOTE_TARGET_COLUMNS,
    TASK_COLUMNS,
    TASK_TARGET_COLUMNS,
    shape_note,
    shape_note_target,
    shape_task,
    shape_task_target,
)
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
DELETED_PREFIX = "deleted_"
"""Resource name prefix of the deleted-records pass; the rows land in the object's own table."""

OBJECTS: dict[str, tuple[str, Callable[[Mapping[str, Any]], dict[str, Any]], dict[str, Any]]] = {
    # resource name -> (REST path and data key, row shaper, column hints)
    "companies": ("companies", shape_company, COMPANY_COLUMNS),
    "people": ("people", shape_person, PERSON_COLUMNS),
    "opportunities": ("opportunities", shape_opportunity, OPPORTUNITY_COLUMNS),
    "notes": ("notes", shape_note, NOTE_COLUMNS),
    "tasks": ("tasks", shape_task, TASK_COLUMNS),
    "note_targets": ("noteTargets", shape_note_target, NOTE_TARGET_COLUMNS),
    "task_targets": ("taskTargets", shape_task_target, TASK_TARGET_COLUMNS),
}


def updated_at_filter(value: datetime | str) -> str:
    """Twenty's filter syntax: one parameter carrying the operator, ``updatedAt[gte]:"<iso>"``.

    The cursor is the shaped ``updated_at`` datetime (the map step runs before the incremental), so it is
    formatted back to the API's UTC ISO form here.
    """
    if isinstance(value, datetime):
        value = value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f'updatedAt[gte]:"{value}"'


def deleted_at_filter(value: datetime | str) -> str:
    """Twenty hides soft-deleted records unless the filter names ``deletedAt``; this asks for the ones
    deleted since the cursor: ``and(deletedAt[is]:NOT_NULL,deletedAt[gte]:"<iso>")``."""
    if isinstance(value, datetime):
        value = value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f'and(deletedAt[is]:NOT_NULL,deletedAt[gte]:"{value}")'


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


def _resource(name: str, deleted: bool) -> EndpointResource:
    """One object's live pass (``updatedAt`` cursor) or its deleted pass (``deletedAt`` cursor)."""
    path, shaper, columns = OBJECTS[name]
    endpoint: Endpoint = {
        "path": path,
        "data_selector": f"data.{path}",
        "incremental": {
            "cursor_path": "deleted_at" if deleted else "updated_at",
            "initial_value": INITIAL_UPDATED_AT,
            "start_param": "filter",
            "convert": deleted_at_filter if deleted else updated_at_filter,
        },
    }
    resource: EndpointResource = {
        "name": f"{DELETED_PREFIX}{name}" if deleted else name,
        "endpoint": endpoint,
        "columns": columns,
        "processing_steps": [{"map": shaper}],
    }
    if deleted:
        resource["table_name"] = name
        endpoint["params"] = {"order_by": "deletedAt[AscNullsLast]"}
    return resource


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
            *(_resource(name, deleted=False) for name in OBJECTS),
            *(_resource(name, deleted=True) for name in OBJECTS),
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
    """Companies, people, opportunities, notes and tasks (with their targets) from a self-hosted Twenty,
    changed or deleted since the last run.

    ``base_url`` is the Twenty instance (``https://twenty.example.com``); ``api_key`` an API key from
    Settings > Developers. ``session`` is injectable for tests.
    """
    if not base_url:
        raise ValueError("twenty needs base_url (sources.twenty.base_url)")
    if not api_key:
        raise ValueError("twenty needs api_key (sources.twenty.api_key)")
    yield from object_resources(base_url, api_key, session)
    yield sync_windows_resource()
