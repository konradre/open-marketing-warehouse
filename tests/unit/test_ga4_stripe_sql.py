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


def test_a_balance_transaction_loaded_while_pending_becomes_available_in_the_view() -> None:
    """The source appends a balance transaction once. Its status changes later; ``available_on`` does not."""
    text = (SQL_DIR / "50_stripe_marts.sql").read_text()
    assert "if(status = 'pending' AND available_on <= now(), 'available', status) AS status" in text


def test_every_stripe_amount_is_divided_by_the_minor_unit_of_its_currency() -> None:
    """One divisor expression, the same wherever an amount leaves minor units: 1 for the zero-decimal
    currencies, 1000 for the three-decimal ones, 100 otherwise. UGX is not zero-decimal to the API
    (docs.stripe.com/currencies, "Special cases": 5 UGX is an amount of 500)."""
    text = (SQL_DIR / "50_stripe_marts.sql").read_text()
    divisors = re.findall(
        r"/ multiIf\(\w*\.?currency IN \(([^)]*)\), 1, \w*\.?currency IN \(([^)]*)\), 1000, 100\)", text
    )
    assert len(divisors) == 5  # invoice amount, subscription price, balance gross, fee and net
    assert len(set(divisors)) == 1
    zero, three = (set(re.findall(r"'(\w+)'", group)) for group in divisors[0])
    assert zero == set(
        [
            "bif",
            "clp",
            "djf",
            "gnf",
            "jpy",
            "kmf",
            "krw",
            "mga",
            "pyg",
            "rwf",
            "vnd",
            "vuv",
            "xaf",
            "xof",
            "xpf",
        ]
    )
    assert three == set(["bhd", "jod", "kwd", "omr", "tnd"])
    assert not re.search(r"toDecimal64\([\w.]+, 2\) / 100\b", text)  # no amount with a fixed divisor
