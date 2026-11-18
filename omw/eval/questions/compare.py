"""Period comparisons: two windows of the same measure, labelled ``current`` and ``previous``.

``query_metrics`` compares with the window of the same length just before (``previous_period``), the same
weekdays seven days earlier (``week``) or the calendar period before (``month``, ``year``). A calendar month
against the calendar month before is ``compare: month``: September against 1 to 31 August, whatever the
lengths of the two months. The ground truth sums each month on its own.
"""

from __future__ import annotations

from functools import partial

from omw.eval.questions.base import MetricStep as M
from omw.eval.questions.base import Question
from omw.eval.truth import change, ratio, total, versus
from omw.eval.world import AUG, LAST_7, SEP, Period, World

Q = partial(Question, category="compare")
USD = {"currency": "USD"}
WEB = {"search_type": "web"}
PERIOD = "period (current or previous)"
PREVIOUS_7 = LAST_7.previous()
PREVIOUS_30 = SEP.previous()
MONTHS = "September 2026 as current and August 2026 as previous"
WEEKS = "the last 7 days as current and the 7 days before them as previous"


def two(current: Period, previous: Period, expression: str, table: str, where: str = "") -> str:
    """One row per period for an aggregate expression."""
    extra = f"{where} AND " if where else ""
    return (
        f"SELECT if({current.sql()}, 'current', 'previous') AS period, {expression} FROM {table} "
        f"WHERE {extra}({current.sql()} OR {previous.sql()}) GROUP BY period ORDER BY period"
    )


def _steps(metric: str, filters: dict[str, str] | None = None) -> tuple[M, ...]:
    """September with the calendar month before it, in one request."""
    return (M((metric,), SEP, filters=filters or {}, compare="month", label="period"),)


def _sum(world: World, table: str, column: str, period: Period, **where: object) -> float:
    return total(world.rows(table, date=period, **where), column)


def _cpc(world: World, period: Period) -> float | None:
    rows = world.rows("meta_ads_campaigns", date=period)
    return ratio(total(rows, "spend"), total(rows, "clicks"))


def _position(world: World, period: Period) -> float | None:
    rows = world.rows("search_console_core", date=period, search_type="web")
    return ratio(sum(r["position"] * r["impressions"] for r in rows), total(rows, "impressions"))


QUESTIONS: tuple[Question, ...] = (
    Q(
        id="meta_spend_sep_vs_aug",
        text=f"How did Meta Ads spend in September 2026 compare with August 2026? Give {MONTHS}.",
        shape="table",
        unit="USD",
        label_name=PERIOD,
        truth=lambda w: versus(
            _sum(w, "meta_ads_campaigns", "spend", SEP), _sum(w, "meta_ads_campaigns", "spend", AUG)
        ),
        sql=two(SEP, AUG, "sum(spend)", "marts.meta_ads_campaigns"),
        metrics=_steps("meta_spend", USD),
    ),
    Q(
        id="revenue_usd_sep_vs_aug",
        text=f"How did US dollar revenue in September 2026 compare with August 2026? Give {MONTHS}.",
        shape="table",
        unit="USD",
        label_name=PERIOD,
        truth=lambda w: versus(
            _sum(w, "stripe_revenue", "amount", SEP, currency="USD"),
            _sum(w, "stripe_revenue", "amount", AUG, currency="USD"),
        ),
        sql=two(SEP, AUG, "sum(amount)", "marts.stripe_revenue", "currency = 'USD'"),
        metrics=_steps("revenue", USD),
    ),
    Q(
        id="opportunities_sep_vs_aug",
        text=(
            f"How many sales opportunities were created in September 2026 against August 2026? Give {MONTHS}."
        ),
        shape="table",
        unit="count",
        label_name=PERIOD,
        truth=lambda w: versus(
            float(len(w.rows("twenty_pipeline", date=SEP))), float(len(w.rows("twenty_pipeline", date=AUG)))
        ),
        sql=two(SEP, AUG, "count()", "marts.twenty_pipeline"),
        metrics=_steps("opportunities"),
    ),
    Q(
        id="new_customers_sep_vs_aug",
        text=(
            f"How many new paying customers did we get in September 2026 against August 2026? Give {MONTHS}."
        ),
        shape="table",
        unit="count",
        label_name=PERIOD,
        truth=lambda w: versus(
            float(len(w.rows("stripe_revenue", date=SEP, billing_reason="subscription_create"))),
            float(len(w.rows("stripe_revenue", date=AUG, billing_reason="subscription_create"))),
        ),
        sql=two(SEP, AUG, "countIf(billing_reason = 'subscription_create')", "marts.stripe_revenue"),
        metrics=_steps("new_customers"),
    ),
    Q(
        id="search_position_sep_vs_aug",
        text=(
            "How did our average position in Google web search change from August 2026 to September "
            f"2026? Give {MONTHS}."
        ),
        shape="table",
        unit="position, 1 = top",
        label_name=PERIOD,
        truth=lambda w: versus(_position(w, SEP), _position(w, AUG)),
        sql=two(
            SEP,
            AUG,
            "sum(position * impressions) / sum(impressions)",
            "marts.search_console_core",
            "search_type = 'web'",
        ),
        metrics=_steps("search_position", WEB),
    ),
    Q(
        id="linkedin_spend_change_pct",
        text="By what percentage did LinkedIn Ads spend change from August 2026 to September 2026?",
        shape="scalar",
        unit="percent, positive for an increase",
        truth=lambda w: change(
            _sum(w, "linkedin_ads_campaigns", "cost", SEP), _sum(w, "linkedin_ads_campaigns", "cost", AUG)
        ),
        sql=(
            f"SELECT 100 * (sumIf(cost, {SEP.sql()}) - sumIf(cost, {AUG.sql()})) / sumIf(cost, {AUG.sql()}) "
            "FROM marts.linkedin_ads_campaigns"
        ),
        metrics=_steps("linkedin_spend", USD),
        combine="pct_change",
    ),
    Q(
        id="google_clicks_l7_vs_previous",
        text=(
            "How many Google Ads clicks did we get over the last 7 days against the 7 days before? Give "
            f"{WEEKS}."
        ),
        shape="table",
        unit="count",
        label_name=PERIOD,
        truth=lambda w: versus(
            _sum(w, "google_ads_campaigns", "clicks", LAST_7),
            _sum(w, "google_ads_campaigns", "clicks", PREVIOUS_7),
        ),
        sql=two(LAST_7, PREVIOUS_7, "sum(clicks)", "marts.google_ads_campaigns"),
        metrics=(M(("google_clicks",), "last_7_days", compare="previous_period", label="period"),),
    ),
    Q(
        id="sessions_l7_vs_previous",
        text=(
            "How many website sessions did we get over the last 7 days against the 7 days before? Give "
            f"{WEEKS}."
        ),
        shape="table",
        unit="count",
        label_name=PERIOD,
        truth=lambda w: versus(
            _sum(w, "ga4_core", "sessions", LAST_7), _sum(w, "ga4_core", "sessions", PREVIOUS_7)
        ),
        sql=two(LAST_7, PREVIOUS_7, "sum(sessions)", "marts.ga4_core"),
        metrics=(M(("sessions",), "last_7_days", compare="week", label="period"),),
    ),
    Q(
        id="meta_cpc_l7_vs_previous",
        text=(
            "How did our Meta Ads cost per click over the last 7 days compare with the 7 days before? "
            f"Give {WEEKS}."
        ),
        shape="table",
        unit="USD",
        label_name=PERIOD,
        truth=lambda w: versus(_cpc(w, LAST_7), _cpc(w, PREVIOUS_7)),
        sql=two(LAST_7, PREVIOUS_7, "sum(spend) / sum(clicks)", "marts.meta_ads_campaigns"),
        naive_sql=two(LAST_7, PREVIOUS_7, "avg(cpc)", "marts.meta_ads_campaigns"),
        metrics=(M(("meta_cpc",), "last_7_days", filters=USD, compare="previous_period", label="period"),),
        tags=("pitfall",),
    ),
    Q(
        id="search_clicks_l30_vs_previous",
        text=(
            "How many Google web search clicks did we get over the last 30 days against the 30 days before? "
            "Give the last 30 days as current and the 30 days before them as previous."
        ),
        shape="table",
        unit="count",
        label_name=PERIOD,
        truth=lambda w: versus(
            _sum(w, "search_console_core", "clicks", SEP, search_type="web"),
            _sum(w, "search_console_core", "clicks", PREVIOUS_30, search_type="web"),
        ),
        sql=two(SEP, PREVIOUS_30, "sum(clicks)", "marts.search_console_core", "search_type = 'web'"),
        metrics=(
            M(("search_clicks",), "last_30_days", filters=WEB, compare="previous_period", label="period"),
        ),
    ),
)
