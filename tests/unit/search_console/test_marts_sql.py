"""sql/10_search_console_marts.sql: one commented view per grain, statements dlt's client can run."""

from __future__ import annotations

import re

from omw.pipelines.marts import SQL_DIR, statements
from omw.sources.search_console.grains import GRAINS, METRICS

MARTS = SQL_DIR / "10_search_console_marts.sql"


def test_one_view_per_grain_with_every_column_commented() -> None:
    stmts = statements(MARTS.read_text())
    assert len(stmts) == len(GRAINS)
    for statement in stmts:
        name = re.search(r"CREATE OR REPLACE VIEW marts\.search_console_(\w+)", statement)
        assert name, statement[:80]
        grain = GRAINS[name.group(1)]
        columns = re.findall(r"^\s+(\w+) [\w()]+ COMMENT '", statement, re.MULTILINE)
        assert columns == ["source", "report", "site_url", "search_type", "date", *grain.dimensions, *METRICS]
        assert f"FROM raw.search_console___{grain.name}" in statement
        assert "never sum" in statement  # the rate columns say so
        assert "DEFINER = CURRENT_USER SQL SECURITY DEFINER AS" in statement  # raw stays invisible to agent


def test_statements_carry_no_inner_semicolons() -> None:
    """dlt's ClickHouse client splits on ';', so a semicolon inside a comment breaks the view."""
    for statement in statements(MARTS.read_text()):
        assert ";" not in statement, statement[:120]
