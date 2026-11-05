from __future__ import annotations

import re

from omw.pipelines.marts import SQL_DIR, statements


def _views(name: str) -> list[str]:
    stmts = statements((SQL_DIR / name).read_text())
    for statement in stmts:
        assert "DEFINER = CURRENT_USER SQL SECURITY DEFINER AS" in statement
        assert ";" not in statement
        columns = re.findall(r"^\s+(\w+) [\w(), ]+ COMMENT '", statement, re.MULTILINE)
        assert columns, statement[:80]
    return [re.search(r"CREATE OR REPLACE VIEW marts\.(\w+)", s).group(1) for s in stmts]  # type: ignore[union-attr]


def test_ga4_views_one_per_grain_with_is_other_on_breakdowns() -> None:
    assert _views("40_ga4_marts.sql") == ["ga4_core", "ga4_channels", "ga4_landing_pages", "ga4_campaigns"]
    text = (SQL_DIR / "40_ga4_marts.sql").read_text()
    assert text.count("is_other Bool COMMENT") == 3


def test_stripe_views_resolve_attribution_invoice_then_subscription_then_customer() -> None:
    assert _views("50_stripe_marts.sql") == [
        "stripe_revenue",
        "stripe_revenue_daily",
        "stripe_subscriptions",
        "stripe_balance",
    ]
    text = (SQL_DIR / "50_stripe_marts.sql").read_text()
    assert "FROM raw.stripe___balance_transactions" in text and "'balance' AS report" in text
    assert (
        "multiIf(i.campaign_key != '', i.campaign_key, s.campaign_key != '', s.campaign_key, c.campaign_key)"
        in text
    )
    assert "WHERE i.status = 'paid' AND i.amount_paid > 0" in text
