"""The schedule as systemd units, for a host that would rather use timers than the scheduler container.

One template service (``omw-sync@.service``) runs ``omw sync <job>``, and one timer per enabled job fires
it at the job's slot (``Persistent=true``: a slot missed while the host was down runs once at boot). The
jobs share a lock, so two never run at the same time, as in the loop. ``omw schedule systemd`` renders
them from the same ``[schedule]`` table the loop reads; ``deploy/systemd/`` holds the rendering of the
defaults.
"""

from __future__ import annotations

from pathlib import Path

from omw.scheduler.jobs import Planned

SERVICE = "omw-sync@.service"
DEFAULT_WORKDIR = "/srv/omw"
CONTAINER_COMMAND = (
    "/usr/bin/docker compose --env-file .env -f deploy/docker-compose.yml --profile scheduler "
    "run --rm -T scheduler"
)
"""Runs ``omw`` in the scheduler image: the same code, user and mounts as the scheduler container."""
LOCK = "/run/lock/omw-sync.lock"


def timer_name(job: str) -> str:
    return f"omw-sync-{job}.timer"


def service_unit(workdir: str = DEFAULT_WORKDIR, command: str = CONTAINER_COMMAND) -> str:
    return f"""# Rendered by `omw schedule systemd`; edit [schedule] in .dlt/config.toml and render again.
[Unit]
Description=open-marketing-warehouse: %i
Documentation=file://{workdir}/docs/DEPLOY.md
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory={workdir}
# One job at a time, as in the scheduler loop: a job waits for the one before it.
ExecStart=/usr/bin/flock {LOCK} {command} sync %i --trigger timer
# `omw sync` exits 1 when the job failed and 3 when a post-sync check failed, after firing the hook itself.
TimeoutStartSec=8h
"""


def timer_unit(planned: Planned) -> str:
    job = planned.job
    return f"""# Rendered by `omw schedule systemd`; edit [schedule] in .dlt/config.toml and render again.
[Unit]
Description=open-marketing-warehouse: {job.name} ({planned.schedule} UTC, {job.summary})

[Timer]
OnCalendar={planned.schedule.on_calendar()}
Persistent=true
Unit=omw-sync@{job.name}.service

[Install]
WantedBy=timers.target
"""


def render(
    plan: list[Planned], workdir: str = DEFAULT_WORKDIR, command: str = CONTAINER_COMMAND
) -> dict[str, str]:
    """File name -> content: the service template and one timer per planned job."""
    units = {SERVICE: service_unit(workdir, command)}
    for planned in plan:
        units[timer_name(planned.job.name)] = timer_unit(planned)
    return units


def write(units: dict[str, str], directory: Path) -> list[Path]:
    """Write ``units`` into ``directory`` and remove timers of jobs that are no longer planned."""
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("omw-sync-*.timer"):
        if stale.name not in units:
            stale.unlink()
    written = []
    for name, content in units.items():
        path = directory / name
        path.write_text(content)
        written.append(path)
    return written
