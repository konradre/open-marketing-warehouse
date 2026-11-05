from __future__ import annotations

import pytest

from omw.metrics import SqlGuardError, assert_single_select


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select date, sum(spend) from marts.meta_ads_campaigns where report = 'campaigns' group by date",
        "WITH a AS (SELECT 1 AS x) SELECT x FROM a",
        "SELECT 1;",
        "SELECT 'insert; drop table x' AS tricky",
        "SELECT 1 -- settings max_result_rows = 1",
        "SELECT /* alter */ 1",
        "SELECT offset_settings FROM marts.x",
    ],
)
def test_accepts_single_selects(sql: str) -> None:
    assert assert_single_select(sql).startswith(sql.strip().rstrip(";")[:6])


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("", "empty"),
        ("SELECT 1; SELECT 2", "more than one"),
        ("INSERT INTO marts.x VALUES (1)", "not a SELECT"),
        ("SET max_result_rows = 1", "not a SELECT"),
        ("SELECT 1 SETTINGS max_result_rows = 100000", "forbidden keyword 'settings'"),
        ("SELECT * FROM system.tables INTO OUTFILE '/tmp/x'", "forbidden keyword 'into outfile'"),
        ("WITH a AS (SELECT 1) SELECT * FROM a; DROP TABLE marts.x", "more than one"),
        ("SELECT 1 FROM x KILL QUERY WHERE 1", "forbidden keyword 'kill'"),
    ],
)
def test_rejects_everything_else(sql: str, reason: str) -> None:
    with pytest.raises(SqlGuardError, match=reason):
        assert_single_select(sql)
