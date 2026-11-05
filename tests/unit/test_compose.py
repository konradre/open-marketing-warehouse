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


def test_ci_boots_the_optional_profiles_in_their_own_job() -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    assert set(jobs) == {"gate", "profiles"} and "needs" not in jobs["profiles"]
    steps = " ".join(str(step.get("run", "")) for step in jobs["profiles"]["steps"])
    assert "deploy/profiles-env.sh .env" in steps and "deploy/profiles-smoke.sh .env" in steps
    assert "down -v" in steps
    for uses in (step["uses"] for job in jobs.values() for step in job["steps"] if "uses" in step):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uses), uses  # pinned by commit sha


def test_profile_scripts_are_executable_and_cover_every_profile_secret() -> None:
    env_script = (REPO / "deploy/profiles-env.sh").read_text()
    smoke = REPO / "deploy/profiles-smoke.sh"
    assert smoke.stat().st_mode & 0o111 and (REPO / "deploy/profiles-env.sh").stat().st_mode & 0o111
    example = (REPO / ".env.example").read_text()
    empty_keys = re.findall(r"^((?:LIBRECHAT|LANGFUSE)_\w+)=$", example, re.MULTILINE)
    assert empty_keys and all(key in env_script for key in empty_keys), empty_keys
    assert "--profile chat --profile observability" in smoke.read_text()
