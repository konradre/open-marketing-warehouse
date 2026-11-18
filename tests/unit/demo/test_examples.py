"""The five example questions, the demo CLI's offline commands and the quickstart's place in the docs."""

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from omw.cli import main
from omw.demo.examples import Example, ask, examples, render, table
from omw.demo.fixtures import Manifest
from omw.demo.restatement import Change, Observation, as_rows, compare
from omw.metrics import Mood, load_catalog
from tests.unit.demo.conftest import TODAY

REPO = Path(__file__).resolve().parents[3]


def _execute(sql: str) -> tuple[list[str], list[list[Any]]]:
    """A warehouse with passing checks and one row for any metric query."""
    if "ops.check_verdicts" in sql:
        return ["source", "check", "report", "status", "detail"], []
    return ["value"], [[1]]


def test_the_five_examples_cover_every_mood() -> None:
    catalog = load_catalog()
    asked = examples(TODAY)
    moods = [ask(example, catalog, _execute, TODAY)["mood"] for example in asked]
    assert moods == [example.expect for example in asked]
    assert len(asked) == 5 and set(moods) == set(Mood)


def test_the_cross_source_example_joins_three_ad_platforms_to_stripe() -> None:
    answer = ask(examples(TODAY)[2], load_catalog(), _execute, TODAY)
    for view in ("meta_ads_campaigns", "google_ads_campaigns", "linkedin_ads_campaigns", "stripe_revenue"):
        assert f"marts.{view}" in answer["sql"]
    assert "marts.campaign_keys" in answer["sql"]


def test_the_served_example_stays_served_whatever_day_it_is() -> None:
    catalog = load_catalog()
    for today in (date(2026, 1, 1), date(2026, 3, 2), date(2027, 12, 31)):
        assert ask(examples(today)[0], catalog, _execute, today)["mood"] == Mood.SERVE


def test_render_shows_the_call_the_mood_and_what_came_with_it() -> None:
    example = Example("Spend?", "why", Mood.DISCLOSE, ["meta_spend"], "last_7_days", ["campaign_key"])
    answer = {
        "mood": "disclose",
        "caveats": ["recent days may change"],
        "columns": ["campaign_key", "meta_spend"],
        "rows": [[f"key_{n}", 1234.5 + n] for n in range(10)],
    }
    text = render(2, example, answer)
    assert '[2/5] Spend?\n  query_metrics({"metrics": ["meta_spend"], "dimensions": ["campaign_key"]' in text
    assert "mood: disclose" in text and "caveat: recent days may change" in text
    assert "key_0         1,234.50" in text and "... 2 more rows" in text
    refused = render(5, example, {"mood": "refuse", "reason": "not additive"})
    assert "reason: not additive" in refused


def test_table_aligns_columns() -> None:
    assert table(["a", "long name"], [["x", 1000], ["yyyy", None]], indent="") == [
        "a     long name",
        "x     1,000",
        "yyyy  -",
    ]


def test_a_restated_day_is_one_that_was_loaded_and_came_back_different() -> None:
    before = {w: Observation(2, 0, {"2026-11-15": 10.0, "2026-11-16": 5.0}) for w in _sources()}
    after = {
        w: Observation(3, 0, {"2026-11-15": 10.0, "2026-11-16": 7.0, "2026-11-17": 4.0}) for w in _sources()
    }
    changes: list[Change] = compare(before, after)
    assert all(change.restated == ["2026-11-16"] and change.new == ["2026-11-17"] for change in changes)
    columns, rows = as_rows(changes)
    assert columns[3:6] == ["days restated", "new days", "duplicate keys"]
    assert rows[0][:6] == ["search_console", 2, 3, 1, 1, 0] and "5.00 -> 7.00 clicks" in rows[0][6]


def _sources() -> list[str]:
    from omw.demo.restatement import WATCHED

    return [watch.source for watch in WATCHED]


def test_generate_command_writes_a_fixture_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "fixtures"
    assert main(["demo", "generate", "--out", str(out), "--today", "2026-11-18", "--days", "60"]) == 0
    manifest = Manifest.read(out)
    assert (manifest.today, manifest.days) == (TODAY, 60)
    assert (out / "initial" / "stripe" / "invoices.json").exists()
    assert (out / "restated" / "twenty" / "timeline_activities.json").exists()
    assert "paying customers" in capsys.readouterr().out


def test_load_and_ask_without_fixtures_fail_before_touching_the_warehouse(tmp_path: Path) -> None:
    assert main(["demo", "load", "--out", str(tmp_path / "none")]) == 1
    assert main(["demo", "ask", "--out", str(tmp_path / "none")]) == 1


def test_make_demo_brings_the_stack_up_and_runs_the_demo() -> None:
    makefile = (REPO / "Makefile").read_text()
    target = re.search(r"^demo: up .*\n\t(.+)$", makefile, re.MULTILINE)
    assert target and target[1].startswith("uv run omw demo run")
    assert "demo demo-drop" in makefile.split(".PHONY:")[1].splitlines()[0]


def test_the_readme_opens_with_the_quickstart() -> None:
    readme = (REPO / "README.md").read_text()
    sections = re.findall(r"^## (.+)$", readme, re.MULTILINE)
    assert sections[0] == "Quickstart: the demo"
    quickstart = readme.split("## Quickstart: the demo")[1].split("\n## ")[0]
    assert "make demo" in quickstart and "No platform account" in quickstart
    for command in (
        "omw demo generate",
        "omw demo load --snapshot restated",
        "omw demo ask",
        "make demo-drop",
    ):
        assert command in quickstart, command


def test_no_module_of_the_package_is_git_ignored() -> None:
    """A package directory named like an ignore rule (``build/``, ``dist/``) would pass every local test
    and be missing from the commit."""
    modules = [str(path.relative_to(REPO)) for path in (REPO / "omw").rglob("*.py")]
    found = subprocess.run(["git", "check-ignore", *modules], cwd=REPO, capture_output=True, text=True)  # noqa: S603, S607
    if found.returncode == 128:
        pytest.skip("not a git checkout")
    assert found.stdout.split() == []
