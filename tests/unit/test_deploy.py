"""What makes the stack runnable on one host: the scheduler service and image, log rotation, the backup
disk, and the configuration that documents the schedule."""

from __future__ import annotations

import re
import tomllib
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

import yaml

from omw.scheduler.jobs import JOBS, build_plan
from omw.scheduler.schedule import parse

REPO = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((REPO / "deploy/docker-compose.yml").read_text())
SERVICES: dict[str, dict[str, Any]] = COMPOSE["services"]
DOCKERFILE = (REPO / "deploy/scheduler/Dockerfile").read_text()
CLICKHOUSE = ElementTree.parse(REPO / "deploy/clickhouse/config.d/omw.xml").getroot()  # noqa: S314 - our own file
CONFIG = tomllib.loads((REPO / ".dlt/config.toml").read_text())


def test_the_scheduler_is_an_optional_service_that_runs_the_loop() -> None:
    scheduler = SERVICES["scheduler"]
    assert scheduler["profiles"] == ["scheduler"]
    assert scheduler["command"] == "${OMW_SCHEDULER_COMMAND:-schedule run}"
    assert scheduler["depends_on"]["clickhouse"]["condition"] == "service_healthy"
    assert scheduler["restart"] == "unless-stopped"
    assert scheduler["healthcheck"]["test"] == ["CMD", "omw", "schedule", "health"]
    assert scheduler["build"] == {"context": "..", "dockerfile": "deploy/scheduler/Dockerfile"}
    assert "ports" not in scheduler  # it calls out; nothing calls it


def test_the_scheduler_reaches_the_warehouse_as_the_pipeline_user_and_gets_secrets_read_only() -> None:
    scheduler = SERVICES["scheduler"]
    env = scheduler["environment"]
    assert env["DESTINATION__CLICKHOUSE__CREDENTIALS__HOST"] == "clickhouse"
    assert env["DESTINATION__CLICKHOUSE__CREDENTIALS__USERNAME"] == "pipeline"
    assert env["DESTINATION__CLICKHOUSE__CREDENTIALS__PASSWORD"] == env["OMW_PIPELINE_PASSWORD"]
    assert env["OMW_PIPELINE_PASSWORD"] == SERVICES["clickhouse"]["environment"]["OMW_PIPELINE_PASSWORD"]
    assert not [key for key in env if "ADMIN" in key]  # it never holds the admin password
    assert "../.dlt:/app/.dlt:ro" in scheduler["volumes"]
    assert "scheduler-state:/var/lib/omw" in scheduler["volumes"] and "scheduler-state" in COMPOSE["volumes"]


def test_clickhouse_and_the_scheduler_share_the_backup_directory() -> None:
    mount = "${OMW_BACKUP_VOLUME:-clickhouse-backups}:/backups"
    assert mount in SERVICES["clickhouse"]["volumes"] and mount in SERVICES["scheduler"]["volumes"]
    assert "clickhouse-backups" in COMPOSE["volumes"]
    assert CLICKHOUSE.findtext("storage_configuration/disks/backups/path") == "/backups/"
    assert CLICKHOUSE.findtext("backups/allowed_disk") == "backups"
    assert "OMW_BACKUP_DIR=/backups" in DOCKERFILE
    # The server writes the files as uid 101; the scheduler prunes them, so it runs as the same uid.
    assert "USER 101:101" in DOCKERFILE


def test_every_container_log_and_the_server_logs_are_rotated() -> None:
    for name, service in SERVICES.items():
        assert service["logging"] == {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "5"},
        }, name
    assert CLICKHOUSE.findtext("logger/size") == "50M" and CLICKHOUSE.findtext("logger/count") == "5"
    for table in ("query_log", "part_log"):
        engine = CLICKHOUSE.findtext(f"{table}/engine") or ""
        assert "TTL event_date + INTERVAL 30 DAY DELETE" in engine, table
    for table in ("trace_log", "metric_log", "asynchronous_metric_log", "text_log"):
        node = CLICKHOUSE.find(table)
        assert node is not None and node.get("remove") == "1", table


def test_ci_builds_the_image_and_runs_it_in_fixture_mode() -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["scheduler"]
    assert "needs" not in job  # beside the gate, never in front of it
    steps = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert "deploy/scheduler-smoke.sh .env" in steps and "--profile scheduler down -v" in steps
    smoke = REPO / "deploy/scheduler-smoke.sh"
    assert smoke.stat().st_mode & 0o111
    script = smoke.read_text()
    assert "set -euo pipefail" in script
    for step in (
        "build scheduler",
        'schedule run --once --fixtures "$fixtures"',
        "backup list",
        "up -d --wait scheduler",
        "scheduler stopped (signal 15)",
    ):
        assert step in script, step
    for job_name in (j.name for j in JOBS if j.name != "meta_ads_daily"):
        assert job_name in script, job_name  # every job must end ok, none is forgotten


def test_the_health_check_reads_a_heartbeat_of_this_container_only() -> None:
    assert "OMW_HEARTBEAT=/tmp/omw-scheduler.heartbeat" in DOCKERFILE
    state = next(v for v in SERVICES["scheduler"]["volumes"] if v.startswith("scheduler-state:"))
    assert not "/tmp".startswith(state.split(":")[1])  # the heartbeat is not on the persistent volume


def test_the_image_is_pinned_and_holds_no_secrets() -> None:
    images = re.findall(r"^FROM (\S+)", DOCKERFILE, re.MULTILINE)
    assert len(images) == 2
    for image in images:
        assert re.fullmatch(r"[\w./-]+:\d+\.\d+\.\d+[\w.-]*", image), image
    copied = re.findall(r"^COPY (?!--from)(.+)$", DOCKERFILE, re.MULTILINE)
    assert copied == [
        "pyproject.toml uv.lock README.md ./",
        "omw ./omw",
        "sql ./sql",
        "metrics ./metrics",
        ".dlt/config.toml ./.dlt/config.toml",
    ]
    assert "--frozen" in DOCKERFILE and 'ENTRYPOINT ["omw"]' in DOCKERFILE
    ignore = (REPO / ".dockerignore").read_text().splitlines()
    assert ignore[2] == "*"  # everything is excluded, then named paths are let back in
    allowed = {line[1:] for line in ignore if line.startswith("!")}
    assert allowed == {"pyproject.toml", "uv.lock", "README.md", "omw", "sql", "metrics", ".dlt/config.toml"}


def test_the_committed_schedule_is_the_default_one_and_names_every_job() -> None:
    schedule = CONFIG["schedule"]
    assert list(schedule) == [job.name for job in JOBS]
    for job in JOBS:
        assert parse(schedule[job.name]) == parse(job.default), job.name
    assert [p.job.name for p in build_plan(schedule)] == [p.job.name for p in build_plan()]
    assert CONFIG["backup"]["keep"] == 7 and CONFIG["checks"]["expiry_warn_days"] == 14
    assert CONFIG["scheduler"] == {"timeout_minutes": 360, "retries": 2, "retry_minutes": 30}


def test_the_examples_document_alerts_expiry_dates_and_where_keys_go() -> None:
    secrets = tomllib.loads((REPO / ".dlt/secrets.toml.example").read_text())
    assert set(secrets["alerts"]) == {
        "webhook_url",
        "email_to",
        "email_from",
        "smtp_host",
        "smtp_port",
        "smtp_username",
        "smtp_password",
    }
    assert secrets["alerts"]["webhook_url"] == "" and secrets["alerts"]["smtp_password"] == ""
    text = (REPO / ".dlt/secrets.toml.example").read_text()
    for source in secrets["sources"]:
        section = text.split(f"[sources.{source}]")[1].split("\n[")[0]
        assert "token_expires_at" in section, source
    # Key files live under .dlt/keys/, a path that is the same on the host and in the scheduler container.
    assert secrets["sources"]["ga4"]["credentials_path"].startswith(".dlt/keys/")
    assert secrets["sources"]["search_console"]["credentials_path"].startswith(".dlt/keys/")
    ignored = (REPO / ".gitignore").read_text().splitlines()
    assert ".dlt/keys/" in ignored and "backups/" in ignored


def test_every_ops_log_table_has_a_retention() -> None:
    """The ops tables are append-only logs on a small host: each one says how long it keeps its rows, and
    the guide tells an existing stack how to add that."""
    sql = REPO / "sql"
    tables = {
        "20_meta_ads_ops.sql": ("ops.pacing_events", "recorded_at"),
        "70_ops_checks.sql": ("ops.check_results", "checked_at"),
        "75_ops_scheduler.sql": ("ops.sync_runs", "started_at"),
    }
    guide = (REPO / "docs/DEPLOY.md").read_text()
    for name, (table, column) in tables.items():
        create = (sql / name).read_text().split(";")[0]
        assert f"CREATE TABLE IF NOT EXISTS {table}" in create, name
        assert f"TTL {column} + INTERVAL 400 DAY" in create, table
        assert f"`{table}`" in guide, table
    for table, column in (("ops.check_results", "checked_at"), ("ops.pacing_events", "recorded_at")):
        assert f"ALTER TABLE {table} MODIFY TTL {column} + INTERVAL 400 DAY" in guide


def test_the_makefile_has_the_operator_targets() -> None:
    makefile = (REPO / "Makefile").read_text()
    for target in (
        "scheduler",
        "schedule",
        "backup",
        "backup-list",
        "backup-verify",
        "restore",
        "systemd-units",
    ):
        assert re.search(rf"^{target}:", makefile, re.MULTILINE), target
    assert "--profile chat --profile observability --profile scheduler down" in makefile


def test_the_deploy_guide_covers_what_an_operator_needs() -> None:
    guide = (REPO / "docs/DEPLOY.md").read_text()
    for heading in (
        "## Local",
        "## VPS",
        "## Secrets",
        "## The schedule",
        "## systemd timers instead",
        "## Alerts",
        "## Credential expiry",
        "## Backup and restore",
        "### Restore drill",
        "## Logs",
        "## Upgrade",
    ):
        assert heading in guide, heading
    for job in JOBS:
        assert f"`{job.name}`" in guide, job.name
