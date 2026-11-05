from __future__ import annotations

import re

from omw.pipelines.marts import SQL_DIR, statements


def test_three_commented_definer_views() -> None:
    stmts = statements((SQL_DIR / "30_google_ads_marts.sql").read_text())
    names = [re.search(r"CREATE OR REPLACE VIEW marts\.(\w+)", s).group(1) for s in stmts]  # type: ignore[union-attr]
    assert names == ["google_ads_campaigns", "google_ads_ad_groups", "google_ads_campaign_list"]
    for statement in stmts:
        assert "DEFINER = CURRENT_USER SQL SECURITY DEFINER AS" in statement
        assert ";" not in statement
        columns = re.findall(r"^\s+(\w+) [\w(), ]+ COMMENT '", statement, re.MULTILINE)
        assert columns, statement[:80]
        if "campaign_list" not in statement:
            assert "cost" in columns and "cost_micros" in columns and "currency" in columns
            assert "toDecimal64(cost_micros, 6) / 1000000 AS cost" in statement
