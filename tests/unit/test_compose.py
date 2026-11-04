"""deploy/docker-compose.yml: localhost-only ports, pinned images, profiles off by default."""

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((REPO / "deploy/docker-compose.yml").read_text())
SERVICES: dict[str, dict[str, Any]] = COMPOSE["services"]
# ${VAR:-default} and ${VAR} as compose interpolates them, so values can be inspected.
_INTERP = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _interpolate(value: str) -> str:
    return _INTERP.sub(lambda m: m.group(2) or "", value)


def test_every_published_port_binds_to_localhost() -> None:
    for name, service in SERVICES.items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name}: {port}"


def test_every_image_is_pinned() -> None:
    for name, service in SERVICES.items():
        image = service["image"]
        tag = image.rsplit(":", 1)[-1] if "@" not in image else image.split("@", 1)[1]
        assert tag and tag != "latest", f"{name}: {image}"
        if "@" in image:
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", tag), f"{name}: {image}"


def test_core_services_have_no_profile_and_optional_ones_do() -> None:
    core = {name for name, s in SERVICES.items() if not s.get("profiles")}
    assert core == {"clickhouse", "mcp-clickhouse"}
    profiles = {p for s in SERVICES.values() for p in s.get("profiles", [])}
    assert profiles == {"chat", "observability"}


def test_clickhouse_has_healthcheck_named_volume_and_bootstrap_mounts() -> None:
    ch = SERVICES["clickhouse"]
    assert ch["healthcheck"]["test"][0] == "CMD"
    volumes = ch["volumes"]
    assert "clickhouse-data:/var/lib/clickhouse" in volumes
    assert "clickhouse-data" in COMPOSE["volumes"]
    assert "../sql:/omw/sql:ro" in volumes
    assert "./clickhouse/initdb:/docker-entrypoint-initdb.d:ro" in volumes
    assert ch["environment"]["CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT"] == "1"


def test_mcp_clickhouse_connects_as_the_agent_user_with_a_token() -> None:
    env = SERVICES["mcp-clickhouse"]["environment"]
    assert env["CLICKHOUSE_USER"] == "agent"
    assert env["CLICKHOUSE_HOST"] == "clickhouse"
    assert env["CLICKHOUSE_MCP_SERVER_TRANSPORT"] == "http"
    assert _INTERP.match(env["CLICKHOUSE_MCP_AUTH_TOKEN"])
    assert "CLICKHOUSE_MCP_AUTH_DISABLED" not in env
    assert env["CLICKHOUSE_PASSWORD"] == SERVICES["clickhouse"]["environment"]["OMW_AGENT_PASSWORD"]
    assert SERVICES["mcp-clickhouse"]["depends_on"]["clickhouse"]["condition"] == "service_healthy"


def test_optional_profiles_do_not_block_the_core_stack() -> None:
    """Compose interpolates inactive profiles too, so their secrets must not use the `:?` form."""
    text = (REPO / "deploy/docker-compose.yml").read_text()
    assert ":?" not in text
