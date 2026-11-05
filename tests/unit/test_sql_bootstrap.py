"""sql/00_*.sql says exactly what DESIGN.md §7 says, before any server is involved."""

import re
from pathlib import Path

import pytest

from tests.expected import AGENT_PROFILE_SETTINGS, AGENT_QUOTA, DATABASES

SQL = Path(__file__).resolve().parents[2] / "sql"


def _read(name: str) -> str:
    return (SQL / name).read_text()


def test_databases_created_if_not_exists() -> None:
    sql = _read("00_01_databases.sql")
    for db in DATABASES:
        assert re.search(rf"CREATE DATABASE IF NOT EXISTS {db}\b", sql), db


def test_users_use_placeholders_and_hashed_passwords() -> None:
    sql = _read("00_02_users.sql")
    for user, placeholder in (("pipeline", "__OMW_PIPELINE_PASSWORD__"), ("agent", "__OMW_AGENT_PASSWORD__")):
        assert re.search(
            rf"CREATE USER IF NOT EXISTS {user} IDENTIFIED WITH sha256_password BY '{placeholder}'", sql
        )
        assert re.search(rf"ALTER USER {user} IDENTIFIED WITH sha256_password BY '{placeholder}'", sql)
    assert "IDENTIFIED WITH plaintext_password" not in sql


@pytest.mark.parametrize(("setting", "value"), sorted(AGENT_PROFILE_SETTINGS.items()))
def test_profile_declares_every_limit_as_const(setting: str, value: str) -> None:
    sql = _read("00_03_profile_agent.sql")
    quoted = f"'{value}'" if not value.lstrip("-").isdigit() else value
    assert re.search(rf"^\s*{setting} = {re.escape(quoted)} CONST,?\s*$", sql, re.MULTILINE), setting


def test_profile_has_no_extra_writable_settings() -> None:
    sql = _read("00_03_profile_agent.sql")
    body = sql.split("SETTINGS", 1)[1].split("TO agent", 1)[0]
    declared = re.findall(r"^\s*(\w+) = \S+ CONST,?\s*$", body, re.MULTILINE)
    assert sorted(declared) == sorted(AGENT_PROFILE_SETTINGS)
    assert re.search(r"CREATE SETTINGS PROFILE OR REPLACE agent_profile", sql)
    assert re.search(r"\bTO agent;", sql)


def test_quota_caps_queries_and_read_bytes_per_hour() -> None:
    sql = _read("00_04_quota_agent.sql")
    assert "CREATE QUOTA OR REPLACE agent_quota" in sql
    assert "KEYED BY user_name" in sql
    limits = f"queries = {AGENT_QUOTA['queries']}, read_bytes = {AGENT_QUOTA['read_bytes']}"
    assert f"FOR INTERVAL 1 hour MAX {limits}" in sql
    assert re.search(r"\bTO agent;", sql)


def test_grants_match_the_boundary() -> None:
    sql = _read("00_05_grants.sql")
    grants = set(re.findall(r"^GRANT (.+?) ON (\S+) TO (\w+);", sql, re.MULTILINE))
    assert grants == {
        ("ALL", "raw.*", "pipeline"),
        ("ALL", "ops.*", "pipeline"),
        ("ALL", "marts.*", "pipeline"),
        ("SELECT", "INFORMATION_SCHEMA.*", "pipeline"),
        ("SELECT", "marts.*", "agent"),
        ("SELECT", "ops.freshness", "agent"),
        ("SELECT", "ops.check_verdicts", "agent"),
    }


def test_bootstrap_script_substitutes_both_placeholders() -> None:
    script = (SQL.parent / "deploy/clickhouse/initdb/00_omw_bootstrap.sh").read_text()
    assert "__OMW_PIPELINE_PASSWORD__|${OMW_PIPELINE_PASSWORD}" in script
    assert "__OMW_AGENT_PASSWORD__|${OMW_AGENT_PASSWORD}" in script
    assert 'files=("$SQL_DIR"/00_*.sql)' in script
