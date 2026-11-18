from __future__ import annotations

from datetime import date

import pytest

from omw.metrics import (
    Catalog,
    MetricRequest,
    SqlGuardError,
    TimeRange,
    assert_single_select,
    compile_request,
)
from omw.metrics.guard import _bare


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
        # comment markers and quotes inside a literal are text, not syntax
        "SELECT 1 FROM marts.x WHERE name = 'a -- b' AND note = 'c /* d'",
        "SELECT 1 FROM marts.x WHERE name = 'it''s; fine' AND note = 'back\\\\slash; \\' quote'",
        # a quoted identifier is a name, whatever it spells
        'SELECT `settings`, "drop" FROM marts.x',
        "SELECT $doc$insert; drop$doc$ AS tricky",
        "SELECT 1 # a hash comment; with a semicolon",
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


@pytest.mark.parametrize(
    "sql",
    [
        # a comment marker inside a string literal does not start a comment
        "SELECT '--'; DROP TABLE marts.campaign_keys",
        "SELECT 1 FROM marts.x WHERE a = '--' INTO OUTFILE '/tmp/x'",
        "SELECT 1 FROM marts.x WHERE a = '--' SETTINGS max_result_rows = 100000",
        "SELECT 1 WHERE a = '/*'; DROP TABLE marts.x; SELECT '*/'",
        # a quote inside a quoted identifier, a heredoc or a comment does not open a string
        "SELECT `'`; DROP TABLE marts.x; SELECT `'`",
        'SELECT "\'"; DROP TABLE marts.x; SELECT "\'"',
        "SELECT $$'$$; DROP TABLE marts.x; SELECT $$'$$",
        "SELECT 1 # '\n; DROP TABLE marts.x # '",
        "SELECT 1 -- '\n; DROP TABLE marts.x -- '",
        "SELECT 1 /* ' */; DROP TABLE marts.x /* ' */",
    ],
)
def test_a_quote_or_comment_marker_cannot_hide_a_second_statement(sql: str) -> None:
    """Each of these is two statements, or one with a forbidden clause, to ClickHouse. The guard used to
    blank comments before strings and knew only single-quoted strings, so all of them passed."""
    with pytest.raises(SqlGuardError, match="more than one|forbidden keyword"):
        assert_single_select(sql)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("SELECT 'never closed", "unterminated ' quote"),
        ("SELECT `never closed", "unterminated ` quote"),
        ("SELECT 1 /* never closed", "unterminated /\\* comment"),
        ("SELECT $tag$ never closed", "not a complete heredoc"),
        ("SELECT 1 #no comment; DROP TABLE marts.x", "does not start a comment"),
    ],
)
def test_refuses_what_it_cannot_read(sql: str, reason: str) -> None:
    with pytest.raises(SqlGuardError, match=reason):
        assert_single_select(sql)


@pytest.mark.parametrize(
    "value",
    ["--", "a -- b", "/* x", "x */", "it's", "back\\slash\\", "'; DROP TABLE marts.x; --", "$$", "#!", "`"],
)
def test_a_hostile_filter_value_stays_one_literal_in_compiled_sql(catalog: Catalog, value: str) -> None:
    """The compiler quotes filter values; the guard must read the result as one statement with the value
    inside one literal, whatever the value contains."""
    request = MetricRequest(
        metrics=["meta_clicks"],
        filters={"campaign_name": value},
        time_range=TimeRange(start=date(2026, 10, 1), end=date(2026, 10, 7)),
    )
    sql = compile_request(catalog, request, today=date(2026, 11, 1)).sql
    assert assert_single_select(sql) == sql
    assert "''" in _bare(sql) and value not in _bare(sql)
