"""mcp-clickhouse from the compose stack runs queries as the `agent` user, inside the profile."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

import pytest
from clickhouse_connect.driver.client import Client
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult

from tests.integration.conftest import setting

pytestmark = pytest.mark.integration


def _mcp_url() -> str:
    return setting("OMW_MCP_URL")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {setting('OMW_MCP_AUTH_TOKEN')}"}


async def _list_tools() -> list[str]:
    async with (
        create_mcp_http_client(headers=_headers()) as http,
        streamable_http_client(_mcp_url(), http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return [tool.name for tool in (await session.list_tools()).tools]


async def _call(tool: str, arguments: dict[str, Any]) -> CallToolResult:
    # mcp-clickhouse sends a whole result as one event; the agent's cap is 10 000 rows, above the
    # client default of 1 MiB per event.
    async with (
        create_mcp_http_client(headers=_headers()) as http,
        streamable_http_client(_mcp_url(), http_client=http, max_sse_event_size=None) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return await session.call_tool(tool, arguments)


def _text(result: CallToolResult) -> str:
    parts = [getattr(block, "text", "") for block in result.content]
    if result.structured_content:
        parts.append(json.dumps(result.structured_content))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def mcp_available(admin_client: Client) -> None:
    try:
        asyncio.run(_list_tools())
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"mcp-clickhouse not reachable at {_mcp_url()}: {exc!r}")


def test_exposes_the_three_tools(mcp_available: None) -> None:
    assert {"list_databases", "list_tables", "run_query"} <= set(asyncio.run(_list_tools()))


def test_run_query_runs_as_the_agent(mcp_available: None) -> None:
    result = asyncio.run(_call("run_query", {"query": "SELECT currentUser() AS who"}))
    assert not result.is_error, _text(result)
    assert "agent" in _text(result)


def test_list_databases_hides_raw(mcp_available: None) -> None:
    result = asyncio.run(_call("list_databases", {}))
    assert not result.is_error, _text(result)
    first = result.content[0]
    databases = json.loads(getattr(first, "text", "[]"))
    assert "marts" in databases
    assert "raw" not in databases


def test_run_query_cannot_write(mcp_available: None, probe_tables: dict[str, str]) -> None:
    result = asyncio.run(_call("run_query", {"query": f"INSERT INTO {probe_tables['marts']} VALUES (4)"}))
    text = _text(result)
    assert result.is_error or "error" in text.lower(), text


def test_run_query_cannot_raise_a_cap(mcp_available: None) -> None:
    result = asyncio.run(_call("run_query", {"query": "SELECT 1 SETTINGS max_result_rows = 100000"}))
    text = _text(result)
    assert result.is_error or "error" in text.lower(), text


def test_run_query_result_breaks_at_max_result_rows(mcp_available: None) -> None:
    result = asyncio.run(_call("run_query", {"query": "SELECT number FROM numbers(5000000)"}))
    assert not result.is_error, _text(result)[:300]
    assert "4999999" not in _text(result)


def test_endpoint_requires_the_bearer_token(mcp_available: None) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    request = urllib.request.Request(
        _mcp_url(),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(request, timeout=10)
    assert info.value.code == 401
