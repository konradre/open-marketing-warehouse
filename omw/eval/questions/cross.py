"""Cross-source measures: ad spend against Stripe revenue, new customers, CRM opportunities and leads.

The right way is the one DESIGN.md §6 describes: aggregate each source on its own to the shared grain, then
join the aggregates. The face-value way joins fact tables row by row and fans out.
"""

from __future__ import annotations

from functools import partial

from omw.eval.questions.base import MetricStep as M
from omw.eval.questions.base import Question
from omw.eval.truth import ad_rows, ad_spend, by, count_by, leads, new_customers, ratio, revenue, total
from omw.eval.world import AUG, LAST_7, Q3, SEP, Period, World

Q = partial(Question, category="cross_source")
USD = {"currency": "USD"}
KEY = ("campaign_key",)

KEYS = (
    "(SELECT source, native_id, argMax(campaign_key, updated_at) AS campaign_key "
    "FROM marts.campaign_keys GROUP BY source, native_id)"
)


def spend_sql(period: Period, currency: str, *group: str) -> str:
    """Ad spend of the three platforms, each aggregated on its own first, then keyed and added up."""
    columns = ", ".join(group)
    lead = f"{columns}, " if group else ""
    tail = f" GROUP BY {columns}" if group else ""
    where = f"currency = '{currency}' AND {period.sql()}"
    inner_group = "campaign_id" + (", date" if "date" in group else "")
    select_date = ", date" if "date" in group else ""
    parts = " UNION ALL ".join(
        f"SELECT '{source}' AS source, campaign_id{select_date}, toFloat64(sum({money})) AS spend, "
        f"sum(clicks) AS clicks FROM marts.{table} WHERE {where} GROUP BY {inner_group}"
        for source, table, money in (
            ("meta_ads", "meta_ads_campaigns", "spend"),
            ("google_ads", "google_ads_campaigns", "cost"),
            ("linkedin_ads", "linkedin_ads_campaigns", "cost"),
        )
    )
    return (
        f"SELECT {lead}sum(spend) AS spend, sum(clicks) AS clicks FROM ({parts}) AS s "
        f"LEFT JOIN {KEYS} AS k ON k.source = s.source AND k.native_id = s.campaign_id{tail}"
    )


def revenue_sql(period: Period, currency: str, *group: str, value: str = "toFloat64(sum(amount))") -> str:
    columns = ", ".join(group)
    lead = f"{columns}, " if group else ""
    tail = f" GROUP BY {columns}" if group else ""
    return (
        f"SELECT {lead}{value} AS value FROM marts.stripe_revenue "
        f"WHERE currency = '{currency}' AND {period.sql()}{tail}"
    )


NEW = "countIf(billing_reason = 'subscription_create')"


def _spend_by_key(world: World, period: Period, currency: str = "USD") -> dict[str, float]:
    return by(ad_rows(world, period, currency), "campaign_key", "spend")


def _roas_by_key(world: World) -> dict[str, float]:
    earned = by(world.rows("stripe_revenue", date=Q3, currency="USD"), "campaign_key", "amount")
    return {key: earned.get(key, 0.0) / spent for key, spent in _spend_by_key(world, Q3).items() if spent}


def _cac_by_key(world: World) -> dict[str, float]:
    signed = count_by(new_customers(world, Q3, "USD"), "campaign_key")
    return {key: spent / signed[key] for key, spent in _spend_by_key(world, Q3).items() if signed.get(key)}


def _pipeline_cac_by_key(world: World) -> dict[str, float]:
    created = count_by(world.rows("twenty_pipeline", date=Q3, currency="USD"), "campaign_key")
    return {key: spent / created[key] for key, spent in _spend_by_key(world, Q3).items() if created.get(key)}


def _roas_by_day(world: World) -> dict[str, float]:
    earned = by(world.rows("stripe_revenue", date=LAST_7, currency="USD"), "date", "amount")
    spent = by(ad_rows(world, LAST_7, "USD"), "date", "spend")
    return {day: earned.get(day, 0.0) / amount for day, amount in spent.items()}


def _fan_out(period: Period, top: str, bottom: str = "sum(toFloat64(c.spend))") -> str:
    """The row-by-row join: every invoice meets every campaign row of its day."""
    return (
        f"SELECT {top} / {bottom} FROM marts.meta_ads_campaigns AS c "
        "INNER JOIN marts.stripe_revenue AS r ON r.date = c.date "
        f"WHERE c.currency = 'USD' AND r.currency = 'USD' AND c.{period.sql()}"
    )


QUESTIONS: tuple[Question, ...] = (
    Q(
        id="ad_spend_usd_sep",
        text=(
            "How much did we spend on advertising across Meta, Google Ads and LinkedIn in September 2026, "
            "counting the US dollar accounts only?"
        ),
        shape="scalar",
        unit="USD",
        truth=lambda w: ad_spend(w, SEP, "USD"),
        sql=f"SELECT spend FROM ({spend_sql(SEP, 'USD')})",
        metrics=(M(("ad_spend",), SEP, filters=USD),),
    ),
    Q(
        id="ad_spend_by_campaign_key_usd_sep",
        text=(
            "How much did each cross-platform campaign key spend in September 2026 across Meta, Google Ads "
            "and LinkedIn, for the US dollar accounts?"
        ),
        shape="table",
        unit="USD",
        label_name="campaign key",
        truth=lambda w: _spend_by_key(w, SEP),
        sql=f"SELECT campaign_key, spend FROM ({spend_sql(SEP, 'USD', 'campaign_key')}) ORDER BY 1",
        naive_sql=(
            "SELECT k.campaign_key, sum(c.spend) FROM marts.meta_ads_campaigns AS c "
            "INNER JOIN marts.campaign_keys AS k ON k.native_id = c.campaign_id "
            f"WHERE c.{SEP.sql()} GROUP BY k.campaign_key ORDER BY 1"
        ),
        metrics=(M(("ad_spend",), SEP, KEY, USD, label="campaign_key"),),
        tags=("pitfall",),
    ),
    Q(
        id="ad_spend_widgets_prospecting_usd_sep",
        text=(
            "How much did the campaign key widgets_prospecting spend across all ad platforms in September "
            "2026, in US dollars?"
        ),
        shape="scalar",
        unit="USD",
        truth=lambda w: _spend_by_key(w, SEP)["widgets_prospecting"],
        sql=(
            f"SELECT spend FROM ({spend_sql(SEP, 'USD', 'campaign_key')}) "
            "WHERE campaign_key = 'widgets_prospecting'"
        ),
        metrics=(M(("ad_spend",), SEP, KEY, USD, label="campaign_key", only="widgets_prospecting"),),
        naive_metrics=(M(("ad_spend",), SEP, filters={"campaign_key": "widgets_prospecting", **USD}),),
        tags=("pitfall",),
    ),
    Q(
        id="roas_usd_sep",
        text=(
            "What was our return on ad spend in September 2026 for US dollars: Stripe revenue divided by "
            "total ad spend on Meta, Google Ads and LinkedIn?"
        ),
        shape="scalar",
        unit="ratio",
        truth=lambda w: ratio(revenue(w, SEP, "USD"), ad_spend(w, SEP, "USD")),
        sql=f"SELECT ({revenue_sql(SEP, 'USD')}) / (SELECT spend FROM ({spend_sql(SEP, 'USD')}))",
        naive_sql=_fan_out(SEP, "sum(toFloat64(r.amount))"),
        metrics=(M(("roas",), SEP, filters=USD),),
        tags=("pitfall",),
    ),
    Q(
        id="roas_by_campaign_usd_q3",
        text=(
            "What was the return on ad spend of each campaign key that had ad spend in the third quarter of "
            "2026, in US dollars (Stripe revenue divided by ad spend across platforms)?"
        ),
        shape="table",
        unit="ratio",
        label_name="campaign key",
        truth=_roas_by_key,
        sql=(
            f"SELECT s.campaign_key, r.value / s.spend FROM ({spend_sql(Q3, 'USD', 'campaign_key')}) AS s "
            f"LEFT JOIN ({revenue_sql(Q3, 'USD', 'campaign_key')}) AS r ON r.campaign_key = s.campaign_key "
            "ORDER BY 1"
        ),
        naive_sql=(
            "SELECT k.campaign_key, sum(toFloat64(r.amount)) / sum(toFloat64(c.spend)) "
            "FROM marts.meta_ads_campaigns AS c "
            "INNER JOIN marts.campaign_keys AS k ON k.source = 'meta_ads' AND k.native_id = c.campaign_id "
            "INNER JOIN marts.stripe_revenue AS r ON r.campaign_key = k.campaign_key AND r.date = c.date "
            f"WHERE r.currency = 'USD' AND c.{Q3.sql()} GROUP BY k.campaign_key ORDER BY 1"
        ),
        metrics=(M(("roas",), Q3, KEY, USD, label="campaign_key"),),
        tags=("pitfall",),
    ),
    Q(
        id="roas_by_day_usd_l7",
        text=(
            "What was our US dollar return on ad spend on each of the last 7 days (Stripe revenue paid that "
            "day divided by ad spend that day)?"
        ),
        shape="table",
        unit="ratio",
        label_name="day (YYYY-MM-DD)",
        truth=_roas_by_day,
        sql=(
            f"SELECT s.date, r.value / s.spend FROM ({spend_sql(LAST_7, 'USD', 'date')}) AS s "
            f"LEFT JOIN ({revenue_sql(LAST_7, 'USD', 'date')}) AS r ON r.date = s.date ORDER BY 1"
        ),
        metrics=(M(("roas",), "last_7_days", ("date",), USD, label="date"),),
    ),
    Q(
        id="roas_eur_q3",
        text="What was our return on ad spend in euros in the third quarter of 2026?",
        shape="scalar",
        unit="ratio",
        truth=lambda w: ratio(revenue(w, Q3, "EUR"), ad_spend(w, Q3, "EUR")),
        sql=f"SELECT ({revenue_sql(Q3, 'EUR')}) / (SELECT spend FROM ({spend_sql(Q3, 'EUR')}))",
        metrics=(M(("roas",), Q3, filters={"currency": "EUR"}),),
    ),
    Q(
        id="cac_usd_sep",
        text=(
            "What was our customer acquisition cost in September 2026 in US dollars: total ad spend divided "
            "by new paying customers (first subscription invoices)?"
        ),
        shape="scalar",
        unit="USD",
        truth=lambda w: ratio(ad_spend(w, SEP, "USD"), len(new_customers(w, SEP, "USD"))),
        sql=f"SELECT (SELECT spend FROM ({spend_sql(SEP, 'USD')})) / ({revenue_sql(SEP, 'USD', value=NEW)})",
        naive_sql=_fan_out(
            SEP, "sum(toFloat64(c.spend))", "countIf(r.billing_reason = 'subscription_create')"
        ),
        metrics=(M(("cac",), SEP, filters=USD),),
        tags=("pitfall",),
    ),
    Q(
        id="cac_by_campaign_usd_q3",
        text=(
            "What was the customer acquisition cost of each campaign key in the third quarter of 2026 in US "
            "dollars, for campaign keys that had both ad spend and new paying customers?"
        ),
        shape="table",
        unit="USD",
        label_name="campaign key",
        truth=_cac_by_key,
        sql=(
            f"SELECT s.campaign_key, s.spend / r.value FROM ({spend_sql(Q3, 'USD', 'campaign_key')}) AS s "
            f"INNER JOIN ({revenue_sql(Q3, 'USD', 'campaign_key', value=NEW)}) AS r "
            "ON r.campaign_key = s.campaign_key WHERE r.value > 0 ORDER BY 1"
        ),
        metrics=(M(("cac",), Q3, KEY, USD, label="campaign_key"),),
    ),
    Q(
        id="pipeline_cac_usd_aug",
        text=(
            "What did one sales opportunity cost us in ad spend in August 2026, in US dollars (total ad "
            "spend divided by US dollar opportunities created)?"
        ),
        shape="scalar",
        unit="USD",
        truth=lambda w: ratio(
            ad_spend(w, AUG, "USD"), len(w.rows("twenty_pipeline", date=AUG, currency="USD"))
        ),
        sql=(
            f"SELECT (SELECT spend FROM ({spend_sql(AUG, 'USD')})) / "
            f"(SELECT count() FROM marts.twenty_pipeline WHERE currency = 'USD' AND {AUG.sql()})"
        ),
        metrics=(M(("pipeline_cac",), AUG, filters=USD),),
    ),
    Q(
        id="pipeline_cac_by_campaign_usd_q3",
        text=(
            "What was the ad spend per sales opportunity of each campaign key in the third quarter of 2026 "
            "in US dollars, for campaign keys that had both ad spend and opportunities?"
        ),
        shape="table",
        unit="USD",
        label_name="campaign key",
        truth=_pipeline_cac_by_key,
        sql=(
            f"SELECT s.campaign_key, s.spend / o.created FROM ({spend_sql(Q3, 'USD', 'campaign_key')}) AS s "
            "INNER JOIN (SELECT campaign_key, count() AS created FROM marts.twenty_pipeline "
            f"WHERE currency = 'USD' AND {Q3.sql()} GROUP BY campaign_key) AS o "
            "ON o.campaign_key = s.campaign_key ORDER BY 1"
        ),
        metrics=(M(("pipeline_cac",), Q3, KEY, USD, label="campaign_key"),),
    ),
    Q(
        id="cost_per_lead_usd_sep",
        text=(
            "What was our cost per lead in September 2026 in US dollars: total ad spend on Meta, Google Ads "
            "and LinkedIn divided by all lead-form submissions on Meta and LinkedIn, organic ones included?"
        ),
        shape="scalar",
        unit="USD",
        truth=lambda w: ratio(ad_spend(w, SEP, "USD"), len(leads(w, SEP, "USD"))),
        sql=(
            f"SELECT (SELECT spend FROM ({spend_sql(SEP, 'USD')})) / ("
            f"(SELECT count() FROM marts.meta_ads_leads WHERE currency = 'USD' AND {SEP.sql()}) + "
            f"(SELECT count() FROM marts.linkedin_ads_leads WHERE currency = 'USD' AND {SEP.sql()}))"
        ),
        metrics=(M(("cost_per_lead",), SEP, filters=USD),),
    ),
    Q(
        id="paid_clicks_sep",
        text=(
            "How many clicks did our ads get in September 2026 across Meta, Google Ads and LinkedIn together?"
        ),
        shape="scalar",
        unit="count",
        truth=lambda w: (
            total(w.rows("meta_ads_campaigns", date=SEP), "clicks")
            + total(w.rows("google_ads_campaigns", date=SEP), "clicks")
            + total(w.rows("linkedin_ads_campaigns", date=SEP), "clicks")
        ),
        sql=(
            f"SELECT (SELECT sum(clicks) FROM marts.meta_ads_campaigns WHERE {SEP.sql()}) + "
            f"(SELECT sum(clicks) FROM marts.google_ads_campaigns WHERE {SEP.sql()}) + "
            f"(SELECT sum(clicks) FROM marts.linkedin_ads_campaigns WHERE {SEP.sql()})"
        ),
        metrics=(
            M(
                ("meta_clicks", "google_clicks", "linkedin_clicks"),
                SEP,
                add=("meta_clicks", "google_clicks", "linkedin_clicks"),
            ),
        ),
    ),
)
