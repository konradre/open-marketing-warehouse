"""Schedule lines, their slots, and the plan built from the job table."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from omw.scheduler.jobs import JOBS, SOURCES, build_plan, enabled_sources, job_named
from omw.scheduler.schedule import Schedule, ScheduleError, parse
from omw.sources.meta_ads.cadences import CADENCES

WEDNESDAY = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def test_a_time_of_day_fires_every_day() -> None:
    schedule = parse("03:15")
    assert schedule == Schedule(3, 15) and schedule.daily and str(schedule) == "03:15"
    assert schedule.latest(WEDNESDAY) == datetime(2026, 11, 18, 3, 15, tzinfo=UTC)
    assert schedule.next(WEDNESDAY) == datetime(2026, 11, 19, 3, 15, tzinfo=UTC)


def test_before_todays_slot_the_latest_one_is_yesterdays() -> None:
    schedule = parse("23:30")
    assert schedule is not None
    assert schedule.latest(WEDNESDAY) == datetime(2026, 11, 17, 23, 30, tzinfo=UTC)
    assert schedule.next(WEDNESDAY) == datetime(2026, 11, 18, 23, 30, tzinfo=UTC)
    at_the_slot = datetime(2026, 11, 18, 23, 30, tzinfo=UTC)
    assert schedule.latest(at_the_slot) == at_the_slot  # a slot is due the moment it arrives
    assert schedule.next(at_the_slot) == at_the_slot + timedelta(days=1)


def test_weekdays_restrict_the_slots() -> None:
    weekly = parse("sun 03:15")
    assert weekly is not None and not weekly.daily and str(weekly) == "sun 03:15"
    assert weekly.latest(WEDNESDAY) == datetime(2026, 11, 15, 3, 15, tzinfo=UTC)
    assert weekly.next(WEDNESDAY) == datetime(2026, 11, 22, 3, 15, tzinfo=UTC)
    twice = parse("Mon,Thu 6:05")
    assert twice is not None and twice.weekdays == frozenset({0, 3})
    assert twice.latest(WEDNESDAY) == datetime(2026, 11, 16, 6, 5, tzinfo=UTC)
    assert twice.next(WEDNESDAY) == datetime(2026, 11, 19, 6, 5, tzinfo=UTC)


def test_slots_are_utc_whatever_zone_the_clock_is_in() -> None:
    schedule = parse("03:15")
    assert schedule is not None
    tokyo = WEDNESDAY.astimezone(timezone(timedelta(hours=9)))
    assert schedule.latest(tokyo) == schedule.latest(WEDNESDAY)


def test_off_disables_and_nonsense_is_refused() -> None:
    assert parse("off") is None and parse("OFF ") is None
    for bad in ("3am", "25:00", "03:75", "funday 03:00", "sun", "03:15 sun", "daily"):
        with pytest.raises(ScheduleError, match="cannot read the schedule"):
            parse(bad)


def test_systemd_calendar_expressions() -> None:
    assert Schedule(3, 0).on_calendar() == "*-*-* 03:00:00 UTC"
    assert Schedule(3, 15, frozenset({6})).on_calendar() == "Sun *-*-* 03:15:00 UTC"
    assert Schedule(6, 5, frozenset({0, 3})).on_calendar() == "Mon,Thu *-*-* 06:05:00 UTC"


def test_the_default_plan_runs_every_source_on_its_cadence() -> None:
    plan = {planned.job.name: planned for planned in build_plan()}
    assert set(plan) == {job.name for job in JOBS}
    assert enabled_sources(list(plan.values())) == set(SOURCES)
    assert set(SOURCES) == {
        "search_console",
        "meta_ads",
        "google_ads",
        "ga4",
        "stripe",
        "linkedin_ads",
        "twenty",
    }
    # DESIGN.md §5: daily, a 7-day re-pull; weekly, a 28-day reconcile.
    assert plan["meta_ads_daily"].job.argv == ("daily",) and CADENCES["daily"] == 7
    assert plan["meta_ads_weekly"].job.argv == ("weekly",) and CADENCES["weekly"] == 28
    assert not plan["meta_ads_weekly"].schedule.daily and len(plan["meta_ads_weekly"].schedule.weekdays) == 1
    for name in ("search_console", "google_ads", "ga4", "stripe", "linkedin_ads", "twenty", "backup"):
        assert plan[name].schedule.daily, name
    assert all(
        planned.job.module.startswith("omw.pipelines.") for planned in plan.values() if planned.job.source
    )


def test_the_weekly_reconcile_replaces_the_daily_re_pull_on_its_day() -> None:
    plan = {planned.job.name: planned.schedule for planned in build_plan()}
    assert plan["meta_ads_weekly"].weekdays == frozenset({6})
    assert plan["meta_ads_daily"].weekdays == frozenset(range(6))
    moved = {p.job.name: p.schedule for p in build_plan({"meta_ads_weekly": "sat 02:00"})}
    assert moved["meta_ads_daily"].weekdays == frozenset({0, 1, 2, 3, 4, 6})
    both_daily = {p.job.name for p in build_plan({"meta_ads_weekly": "02:00"})}
    assert "meta_ads_daily" not in both_daily and "meta_ads_weekly" in both_daily
    no_weekly = {p.job.name: p.schedule for p in build_plan({"meta_ads_weekly": "off"})}
    assert "meta_ads_weekly" not in no_weekly and no_weekly["meta_ads_daily"].daily


def test_off_takes_a_source_out_of_the_plan_and_out_of_the_checks() -> None:
    plan = build_plan({"linkedin_ads": "off", "twenty": "09:30"})
    names = [planned.job.name for planned in plan]
    assert "linkedin_ads" not in names and "linkedin_ads" not in enabled_sources(plan)
    assert next(p.schedule for p in plan if p.job.name == "twenty") == Schedule(9, 30)
    only_weekly = build_plan({"meta_ads_daily": "off"})
    assert "meta_ads" in enabled_sources(only_weekly)


def test_a_misspelt_job_is_an_error_not_a_silent_default() -> None:
    with pytest.raises(ScheduleError, match="linkedin, which is not a job"):
        build_plan({"linkedin": "off"})
    with pytest.raises(ScheduleError, match="no job named 'meta'"):
        job_named("meta")
    assert job_named("backup").kind == "backup"
