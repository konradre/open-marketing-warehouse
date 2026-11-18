"""Ground truth: small aggregations over the seeded ``World``'s rows, and the value a question expects.

Nothing here touches ClickHouse. The numbers come from the generated rows, so a wrong view, a wrong
compiled query or a wrong reference query disagrees with them instead of agreeing with itself.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from omw.eval.questions.base import BLANK, Question
from omw.eval.world import Period, Row, World

Key = str | Callable[[Row], Any]


def total(rows: Iterable[Row], column: str) -> float:
    return float(sum(row[column] for row in rows))


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def label_of(value: Any) -> str:
    """A row label as text: ISO dates, lower-case booleans, ``(none)`` for an empty one."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or BLANK


def _key(row: Row, key: Key) -> str:
    return label_of(key(row) if callable(key) else row[key])


def by(rows: Iterable[Row], key: Key, column: str) -> dict[str, float]:
    """Sum of ``column`` per label."""
    found: dict[str, float] = {}
    for row in rows:
        label = _key(row, key)
        found[label] = found.get(label, 0.0) + float(row[column])
    return found


def count_by(rows: Iterable[Row], key: Key) -> dict[str, float]:
    found: dict[str, float] = {}
    for row in rows:
        label = _key(row, key)
        found[label] = found.get(label, 0.0) + 1.0
    return found


def distinct(rows: Iterable[Row], column: str) -> float:
    return float(len({row[column] for row in rows}))


def divide(
    numerators: dict[str, float], denominators: dict[str, float], scale: float = 1.0
) -> dict[str, float]:
    """Label by label, for the labels whose denominator is not zero."""
    return {
        label: scale * numerators.get(label, 0.0) / bottom for label, bottom in denominators.items() if bottom
    }


def largest(values: dict[str, float], n: int) -> list[tuple[str, float]]:
    return sorted(values.items(), key=lambda item: (-item[1], item[0]))[:n]


def first(values: dict[str, float]) -> str:
    return largest(values, 1)[0][0]


def versus(current: float | None, previous: float | None) -> dict[str, float | None]:
    return {"current": current, "previous": previous}


def change(current: float, previous: float) -> float:
    """Percentage change from ``previous`` to ``current``."""
    return 100 * (current - previous) / previous


# ---- cross-source building blocks: each source aggregated on its own, then combined ----------------------

AD_TABLES = (
    ("meta_ads", "meta_ads_campaigns", "spend"),
    ("google_ads", "google_ads_campaigns", "cost"),
    ("linkedin_ads", "linkedin_ads_campaigns", "cost"),
)


def ad_rows(world: World, period: Period, currency: str | None = None) -> list[Row]:
    """Every ad platform's campaign days as (date, currency, campaign_key, spend, clicks)."""
    found: list[Row] = []
    for source, table, money in AD_TABLES:
        for row in world.rows(table, date=period):
            if currency and row["currency"] != currency:
                continue
            found.append(
                {
                    "date": row["date"],
                    "currency": row["currency"],
                    "campaign_key": world.key_of(source, row["campaign_id"]),
                    "spend": row[money],
                    "clicks": row["clicks"],
                }
            )
    return found


def ad_spend(world: World, period: Period, currency: str | None = None) -> float:
    return total(ad_rows(world, period, currency), "spend")


def revenue(world: World, period: Period, currency: str | None = None, **where: Any) -> float:
    if currency:
        where["currency"] = currency
    return total(world.rows("stripe_revenue", date=period, **where), "amount")


def new_customers(world: World, period: Period, currency: str | None = None) -> list[Row]:
    where = {"currency": currency} if currency else {}
    return world.rows("stripe_revenue", date=period, billing_reason="subscription_create", **where)


def leads(world: World, period: Period, currency: str | None = None) -> list[Row]:
    where = {"currency": currency} if currency else {}
    return world.rows("meta_ads_leads", date=period, **where) + world.rows(
        "linkedin_ads_leads", date=period, **where
    )


# ---- the value a question expects --------------------------------------------------------------------------


@dataclass(frozen=True)
class Expected:
    """The right answer in one of three shapes; all ``None`` for a trap that has no number."""

    value: float | None = None
    label: str | None = None
    rows: tuple[tuple[str, float | None], ...] | None = None
    ordered: bool = False

    def to_dict(self) -> dict[str, Any]:
        if self.rows is not None:
            return {"rows": [list(row) for row in self.rows], "ordered": self.ordered}
        if self.label is not None:
            return {"label": self.label}
        return {"value": self.value}


def expected(question: Question, world: World) -> Expected | None:
    """The ground truth of ``question`` on ``world``, or ``None`` when it has none (a pure trap)."""
    if question.truth is None:
        return None
    raw = question.truth(world)
    if question.shape == "label":
        return Expected(label=label_of(raw))
    if question.shape == "scalar":
        return Expected(value=None if raw is None else float(raw))
    if isinstance(raw, dict):
        items = largest(raw, question.top) if question.top else sorted(raw.items())
    else:
        items = list(raw)
    rows = tuple((label_of(label), None if value is None else float(value)) for label, value in items)
    return Expected(rows=rows, ordered=bool(question.top))
