"""The example files document every key the stack and the pipelines read."""

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _env_keys(path: Path) -> dict[str, str]:
    keys: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            keys[key.strip()] = value.strip()
    return keys


def test_env_example_documents_every_compose_variable() -> None:
    compose = (REPO / "deploy/docker-compose.yml").read_text()
    used = set(re.findall(r"\$\{(\w+)", compose))
    documented = set(_env_keys(REPO / ".env.example"))
    assert used <= documented, sorted(used - documented)


def test_env_example_has_no_real_looking_secrets() -> None:
    secret_words = {"PASSWORD", "SECRET", "KEY", "TOKEN", "SALT", "IV"}
    for key, value in _env_keys(REPO / ".env.example").items():
        if secret_words & set(key.split("_")):
            assert value == "" or value.endswith(("-dev-password", "-dev-token")), key


def test_dlt_secrets_example_parses_and_covers_every_source() -> None:
    data = tomllib.loads((REPO / ".dlt/secrets.toml.example").read_text())
    creds = data["destination"]["clickhouse"]["credentials"]
    assert creds["username"] == "pipeline"
    assert creds["database"] == "raw"
    assert set(data["sources"]) == {
        "meta_ads",
        "google_ads",
        "ga4",
        "search_console",
        "linkedin_ads",
        "twenty",
        "stripe",
    }


def test_gitignore_keeps_the_examples_and_drops_the_secrets() -> None:
    lines = (REPO / ".gitignore").read_text().splitlines()
    assert ".env" in lines
    assert ".dlt/secrets.toml" in lines
    assert "!.env.example" in lines
