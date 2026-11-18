"""Warehouse connections for the evaluation, from the same ``OMW_*`` settings the compose stack and the
tests use: the environment first, then the repo-root ``.env`` (``.env.example``), then the laptop dev
defaults."""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Any

import clickhouse_connect

DOTENV = Path(__file__).resolve().parents[2] / ".env"
DEV_PASSWORDS = {"pipeline": "pipeline-dev-password", "agent": "agent-dev-password"}


@cache
def dotenv(path: Path = DOTENV) -> dict[str, str]:
    """``KEY=value`` lines of the ``.env`` file, when there is one."""
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


def setting(name: str, default: str) -> str:
    return os.environ.get(name) or dotenv().get(name) or default


def client(user: str) -> Any:
    """clickhouse-connect as ``pipeline`` (to seed) or ``agent`` (to answer)."""
    return clickhouse_connect.get_client(
        host=setting("OMW_CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(setting("OMW_CLICKHOUSE_HTTP_PORT", "8123")),
        username=user,
        password=setting(f"OMW_{user.upper()}_PASSWORD", DEV_PASSWORDS[user]),
        connect_timeout=5,
        send_receive_timeout=60,
    )


def mcp_endpoint() -> tuple[str, str]:
    """(url, bearer token) of mcp-clickhouse."""
    return setting("OMW_MCP_URL", "http://127.0.0.1:8000/mcp"), setting("OMW_MCP_AUTH_TOKEN", "mcp-dev-token")
