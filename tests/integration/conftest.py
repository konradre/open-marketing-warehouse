"""Connections to the stack from deploy/docker-compose.yml.

Reads `.env` at the repo root when present, then the environment, then the compose dev defaults. Skips
the whole directory when ClickHouse is not reachable, unless OMW_REQUIRE_CLICKHOUSE=1 (CI), which fails.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import clickhouse_connect
import pytest
from clickhouse_connect.driver.client import Client

REPO = Path(__file__).resolve().parents[2]

DEFAULTS = {
    "OMW_CLICKHOUSE_HOST": "127.0.0.1",
    "OMW_CLICKHOUSE_HTTP_PORT": "8123",
    "OMW_ADMIN_PASSWORD": "admin-dev-password",
    "OMW_PIPELINE_PASSWORD": "pipeline-dev-password",
    "OMW_AGENT_PASSWORD": "agent-dev-password",
    "OMW_MCP_URL": "http://127.0.0.1:8000/mcp",
    "OMW_MCP_AUTH_TOKEN": "mcp-dev-token",
}


def _dotenv() -> dict[str, str]:
    path = REPO / ".env"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


_DOTENV = _dotenv()


def setting(name: str) -> str:
    return os.environ.get(name) or _DOTENV.get(name) or DEFAULTS[name]


def connect(user: str, password: str) -> Client:
    return clickhouse_connect.get_client(
        host=setting("OMW_CLICKHOUSE_HOST"),
        port=int(setting("OMW_CLICKHOUSE_HTTP_PORT")),
        username=user,
        password=password,
        connect_timeout=5,
        send_receive_timeout=60,
    )


@pytest.fixture(scope="session")
def admin_client() -> Iterator[Client]:
    try:
        client = connect("admin", setting("OMW_ADMIN_PASSWORD"))
        client.command("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - any connection failure means "no stack"
        if os.environ.get("OMW_REQUIRE_CLICKHOUSE") == "1":
            raise
        pytest.skip(f"ClickHouse not reachable ({exc.__class__.__name__}); run `make up`")
    yield client
    client.close()


@pytest.fixture(scope="session")
def agent_client(admin_client: Client) -> Iterator[Client]:
    client = connect("agent", setting("OMW_AGENT_PASSWORD"))
    yield client
    client.close()


@pytest.fixture(scope="session")
def pipeline_client(admin_client: Client) -> Iterator[Client]:
    client = connect("pipeline", setting("OMW_PIPELINE_PASSWORD"))
    yield client
    client.close()


@pytest.fixture(scope="session")
def probe_tables(admin_client: Client) -> Iterator[dict[str, str]]:
    """One small table in each database, plus ops.freshness if T7 has not created it yet."""
    tables = {"raw": "raw.t1_probe", "ops": "ops.t1_probe", "marts": "marts.t1_probe"}
    created_freshness = False
    for name in tables.values():
        admin_client.command(f"CREATE TABLE IF NOT EXISTS {name} (id UInt32) ENGINE = MergeTree ORDER BY id")
        admin_client.command(f"INSERT INTO {name} VALUES (1), (2), (3)")
    if not admin_client.command("EXISTS TABLE ops.freshness"):
        admin_client.command(
            "CREATE TABLE ops.freshness (source LowCardinality(String), synced_at DateTime) "
            "ENGINE = MergeTree ORDER BY source"
        )
        created_freshness = True
    yield tables
    for name in tables.values():
        admin_client.command(f"DROP TABLE IF EXISTS {name}")
    if created_freshness:
        admin_client.command("DROP TABLE IF EXISTS ops.freshness")
