"""The package layout from DESIGN.md §8 exists and imports."""

import importlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "name", ["omw", "omw.sources", "omw.pacing", "omw.pipelines", "omw.checks", "omw.metrics"]
)
def test_subpackage_imports(name: str) -> None:
    assert importlib.import_module(name).__doc__


@pytest.mark.parametrize(
    "relative",
    [
        "deploy/docker-compose.yml",
        "deploy/clickhouse/initdb/00_omw_bootstrap.sh",
        ".github/workflows/ci.yml",
        ".dlt/secrets.toml.example",
        ".env.example",
        "Makefile",
        "tests/fixtures",
    ],
)
def test_skeleton_files_exist(relative: str) -> None:
    assert (REPO / relative).exists(), relative


def test_bootstrap_sql_is_ordered() -> None:
    files = sorted(p.name for p in (REPO / "sql").glob("00_*.sql"))
    assert files == [
        "00_01_databases.sql",
        "00_02_users.sql",
        "00_03_profile_agent.sql",
        "00_04_quota_agent.sql",
        "00_05_grants.sql",
    ]
