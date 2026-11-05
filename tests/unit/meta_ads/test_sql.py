from __future__ import annotations

import re

from omw.pipelines.marts import SQL_DIR, statements
from omw.pipelines.ops_log import PACING_EVENT_COLUMNS


def test_ops_table_matches_the_event_columns() -> None:
    stmts = statements((SQL_DIR / "20_meta_ads_ops.sql").read_text())
    assert len(stmts) == 1 and "CREATE TABLE IF NOT EXISTS ops.pacing_events" in stmts[0]
    columns = re.findall(r"^\s+(\w+) [\w()]+ COMMENT '", stmts[0], re.MULTILINE)
    assert tuple(columns) == PACING_EVENT_COLUMNS
    assert ";" not in stmts[0]


def test_marts_views_are_commented_definer_views() -> None:
    for grain, extra in (("campaigns", ()), ("ads", ("adset_id", "adset_name", "ad_id", "ad_name"))):
        stmts = statements((SQL_DIR / f"20_meta_ads_marts_{grain}.sql").read_text())
        assert len(stmts) == 1
        statement = stmts[0]
        assert f"CREATE OR REPLACE VIEW marts.meta_ads_{grain}" in statement
        assert f"FROM raw.meta_ads___insights_{grain}" in statement
        assert "DEFINER = CURRENT_USER SQL SECURITY DEFINER AS" in statement
        columns = re.findall(r"^\s+(\w+) [\w(), ]+ COMMENT '", statement, re.MULTILINE)
        assert columns[:7] == [
            "source",
            "report",
            "account_id",
            "currency",
            "date",
            "campaign_id",
            "campaign_name",
        ]
        assert columns[7 : 7 + len(extra)] == list(extra)
        assert ";" not in statement
