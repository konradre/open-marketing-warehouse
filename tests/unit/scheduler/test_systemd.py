"""The systemd rendering of the schedule, and that the committed units are the rendering of the defaults."""

from __future__ import annotations

from pathlib import Path

from omw import cli
from omw.scheduler import systemd
from omw.scheduler.jobs import build_plan

REPO = Path(__file__).resolve().parents[3]
UNITS = REPO / "deploy/systemd"


def test_the_committed_units_are_the_default_schedule() -> None:
    rendered = systemd.render(build_plan())
    committed = {
        path.name: path.read_text() for path in UNITS.iterdir() if path.suffix in (".timer", ".service")
    }
    assert committed == rendered, "run `make systemd-units` after changing the job table or its rendering"


def test_one_timer_per_job_catching_up_after_downtime() -> None:
    units = systemd.render(build_plan())
    assert len(units) == 11 and systemd.SERVICE in units
    weekly = units["omw-sync-meta_ads_weekly.timer"]
    assert "OnCalendar=Sun *-*-* 03:15:00 UTC" in weekly and "Unit=omw-sync@meta_ads_weekly.service" in weekly
    # The weekly reconcile takes Sunday; the daily re-pull has the other six days.
    assert "OnCalendar=Mon,Tue,Wed,Thu,Fri,Sat *-*-* 03:15:00 UTC" in units["omw-sync-meta_ads_daily.timer"]
    for name, unit in units.items():
        if name.endswith(".timer"):
            assert "Persistent=true" in unit and "WantedBy=timers.target" in unit, name


def test_the_service_runs_one_job_at_a_time_through_omw_sync() -> None:
    service = systemd.render(build_plan())[systemd.SERVICE]
    assert "Type=oneshot" in service
    assert "ExecStart=/usr/bin/flock /run/lock/omw-sync.lock /usr/bin/docker compose" in service
    assert service.count("ExecStart=") == 1 and "run --rm -T scheduler sync %i --trigger timer" in service
    host = systemd.service_unit("/opt/omw", "/usr/local/bin/uv run omw")
    assert "WorkingDirectory=/opt/omw" in host
    assert (
        "ExecStart=/usr/bin/flock /run/lock/omw-sync.lock /usr/local/bin/uv run omw sync %i --trigger timer"
        in host
    )


def test_rendering_follows_the_schedule_and_removes_stale_timers(tmp_path: Path) -> None:
    (tmp_path / "omw-sync-linkedin_ads.timer").write_text("old")
    (tmp_path / "unrelated.timer").write_text("keep")
    plan = build_plan({"linkedin_ads": "off", "twenty": "mon,thu 09:30"})
    written = systemd.write(systemd.render(plan), tmp_path)
    names = {path.name for path in tmp_path.iterdir()}
    assert "omw-sync-linkedin_ads.timer" not in names and "unrelated.timer" in names
    assert len(written) == 10
    assert "OnCalendar=Mon,Thu *-*-* 09:30:00 UTC" in (tmp_path / "omw-sync-twenty.timer").read_text()


def test_the_cli_prints_or_writes_the_units(tmp_path: Path, capsys: object) -> None:
    assert cli.main(["schedule", "systemd", "--output", str(tmp_path), "--workdir", "/opt/omw"]) == 0
    assert "WorkingDirectory=/opt/omw" in (tmp_path / systemd.SERVICE).read_text()
    assert cli.main(["schedule", "systemd"]) == 0
