"""Revenue (Stripe) and the sales pipeline (Twenty CRM)."""

from __future__ import annotations

from functools import partial

from omw.eval.questions.base import MetricStep as M
from omw.eval.questions.base import Question
from omw.eval.truth import by, count_by, distinct, first, ratio, total
from omw.eval.world import AUG, Q3, SEP

R = partial(Question, category="revenue")
P = partial(Question, category="pipeline")
USD = {"currency": "USD"}
REVENUE = "marts.stripe_revenue"
BALANCE = "marts.stripe_balance"
PIPELINE = "marts.twenty_pipeline"
NET = "('charge', 'refund', 'dispute', 'dispute_reversal')"

REVENUE_QUESTIONS: tuple[Question, ...] = (
    R(
        id="revenue_usd_sep",
        text="How much revenue did we collect in US dollars in September 2026?",
        shape="scalar",
        unit="USD",
        truth=lambda w: total(w.rows("stripe_revenue", date=SEP, currency="USD"), "amount"),
        sql=f"SELECT sum(amount) FROM {REVENUE} WHERE currency = 'USD' AND {SEP.sql()}",
        metrics=(M(("revenue",), SEP, filters=USD),),
    ),
    R(
        id="revenue_by_currency_sep",
        text="How much revenue did we collect in September 2026, in each invoice currency?",
        shape="table",
        unit="invoice currency",
        label_name="currency",
        truth=lambda w: by(w.rows("stripe_revenue", date=SEP), "currency", "amount"),
        sql=f"SELECT currency, sum(amount) FROM {REVENUE} WHERE {SEP.sql()} GROUP BY currency ORDER BY 1",
        metrics=(M(("revenue",), SEP, ("currency",), label="currency"),),
    ),
    R(
        id="paid_invoices_aug",
        text="How many invoices were paid in August 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: float(len(w.rows("stripe_revenue", date=AUG))),
        sql=f"SELECT count() FROM {REVENUE} WHERE {AUG.sql()}",
        metrics=(M(("paid_invoices",), AUG),),
    ),
    R(
        id="new_customers_aug",
        text="How many new paying customers (first subscription invoices paid) did we get in August 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: float(len(w.rows("stripe_revenue", date=AUG, billing_reason="subscription_create"))),
        sql=f"SELECT countIf(billing_reason = 'subscription_create') FROM {REVENUE} WHERE {AUG.sql()}",
        metrics=(M(("new_customers",), AUG),),
    ),
    R(
        id="revenue_by_campaign_usd_q3",
        text=(
            "How much US dollar revenue did each campaign key bring in the third quarter of 2026 "
            "(July to September)? Include revenue that has no campaign key."
        ),
        shape="table",
        unit="USD",
        label_name="campaign key",
        truth=lambda w: by(w.rows("stripe_revenue", date=Q3, currency="USD"), "campaign_key", "amount"),
        sql=(
            f"SELECT campaign_key, sum(amount) FROM {REVENUE} WHERE currency = 'USD' AND {Q3.sql()} "
            "GROUP BY campaign_key ORDER BY 1"
        ),
        metrics=(M(("revenue",), Q3, ("campaign_key",), USD, label="campaign_key"),),
    ),
    R(
        id="unattributed_revenue_usd_sep",
        text="How much US dollar revenue in September 2026 carried no attribution at all?",
        shape="scalar",
        unit="USD",
        truth=lambda w: total(w.rows("stripe_revenue", date=SEP, currency="USD", attributed=False), "amount"),
        sql=f"SELECT sum(amount) FROM {REVENUE} WHERE currency = 'USD' AND NOT attributed AND {SEP.sql()}",
        naive_sql=(
            f"SELECT sum(amount) FROM {REVENUE} WHERE currency = 'USD' AND campaign_key = '' AND {SEP.sql()}"
        ),
        metrics=(M(("revenue",), SEP, filters={"attributed": False, **USD}),),
        tags=("pitfall",),
    ),
    R(
        id="revenue_newsletter_usd_q3",
        text=(
            "How much US dollar revenue came from customers attributed to the newsletter (utm_source "
            "newsletter) in the third quarter of 2026?"
        ),
        shape="scalar",
        unit="USD",
        truth=lambda w: total(
            w.rows("stripe_revenue", date=Q3, currency="USD", utm_source="newsletter"), "amount"
        ),
        sql=(
            f"SELECT sum(amount) FROM {REVENUE} WHERE currency = 'USD' AND utm_source = 'newsletter' AND "
            f"{Q3.sql()}"
        ),
        metrics=(M(("revenue",), Q3, filters={"utm_source": "newsletter", **USD}),),
    ),
    R(
        id="net_revenue_usd_sep",
        text=(
            "What was our net revenue in US dollars in September 2026, after refunds, disputes and Stripe "
            "fees?"
        ),
        shape="scalar",
        unit="USD",
        truth=lambda w: total(
            w.rows(
                "stripe_balance",
                date=SEP,
                currency="USD",
                reporting_category=("charge", "refund", "dispute", "dispute_reversal"),
            ),
            "net",
        ),
        sql=(
            f"SELECT sumIf(net, reporting_category IN {NET}) FROM {BALANCE} WHERE currency = 'USD' AND "
            f"{SEP.sql()}"
        ),
        naive_sql=f"SELECT sum(net) FROM {BALANCE} WHERE currency = 'USD' AND {SEP.sql()}",
        metrics=(M(("net_revenue",), SEP, filters=USD),),
        tags=("pitfall",),
    ),
    R(
        id="refunds_usd_q3",
        text="How much did we refund to customers in US dollars in the third quarter of 2026?",
        shape="scalar",
        unit="USD",
        truth=lambda w: (
            -total(w.rows("stripe_balance", date=Q3, currency="USD", reporting_category="refund"), "gross")
        ),
        sql=(
            f"SELECT -sumIf(gross, reporting_category = 'refund') FROM {BALANCE} WHERE currency = 'USD' "
            f"AND {Q3.sql()}"
        ),
        metrics=(M(("refunds",), Q3, filters=USD),),
    ),
    R(
        id="stripe_fees_usd_aug",
        text="How much did Stripe charge us in processing fees on US dollar payments in August 2026?",
        shape="scalar",
        unit="USD",
        truth=lambda w: total(
            w.rows(
                "stripe_balance",
                date=AUG,
                currency="USD",
                reporting_category=("charge", "refund", "dispute", "dispute_reversal"),
            ),
            "fee",
        ),
        sql=(
            f"SELECT sumIf(fee, reporting_category IN {NET}) FROM {BALANCE} WHERE currency = 'USD' AND "
            f"{AUG.sql()}"
        ),
        metrics=(M(("stripe_fees",), AUG, filters=USD),),
    ),
    R(
        id="paying_customers_sep",
        text="How many distinct customers paid at least one invoice in September 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: distinct(w.rows("stripe_revenue", date=SEP), "customer_id"),
        sql=f"SELECT uniqExact(customer_id) FROM {REVENUE} WHERE {SEP.sql()}",
        naive_sql=(f"SELECT sum(customers) FROM marts.stripe_revenue_daily WHERE {SEP.sql()}"),
        metrics=(M(("paying_customers",), SEP),),
        tags=("pitfall", "grain_refused"),
    ),
    R(
        id="active_subscriptions",
        text="How many subscriptions are active right now?",
        shape="scalar",
        unit="count",
        truth=lambda w: float(len(w.rows("stripe_subscriptions", status="active"))),
        sql="SELECT count() FROM marts.stripe_subscriptions WHERE status = 'active'",
        tags=("not_in_catalog",),
    ),
)

PIPELINE_QUESTIONS: tuple[Question, ...] = (
    P(
        id="opportunities_sep",
        text="How many sales opportunities were created in September 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: float(len(w.rows("twenty_pipeline", date=SEP))),
        sql=f"SELECT count() FROM {PIPELINE} WHERE {SEP.sql()}",
        metrics=(M(("opportunities",), SEP),),
    ),
    P(
        id="pipeline_amount_usd_aug",
        text="What is the total US dollar amount of the opportunities created in August 2026?",
        shape="scalar",
        unit="USD",
        truth=lambda w: total(w.rows("twenty_pipeline", date=AUG, currency="USD"), "amount"),
        sql=f"SELECT sum(amount) FROM {PIPELINE} WHERE currency = 'USD' AND {AUG.sql()}",
        metrics=(M(("pipeline_amount",), AUG, filters=USD),),
    ),
    P(
        id="won_opportunities_q3",
        text="How many of the opportunities created in the third quarter of 2026 have been won?",
        shape="scalar",
        unit="count",
        truth=lambda w: float(len(w.rows("twenty_pipeline", date=Q3, stage="CUSTOMER"))),
        sql=f"SELECT countIf(stage = 'CUSTOMER') FROM {PIPELINE} WHERE {Q3.sql()}",
        metrics=(M(("won_opportunities",), Q3),),
    ),
    P(
        id="won_amount_usd_q3",
        text="What is the US dollar amount of the won opportunities created in the third quarter of 2026?",
        shape="scalar",
        unit="USD",
        truth=lambda w: total(w.rows("twenty_pipeline", date=Q3, currency="USD", stage="CUSTOMER"), "amount"),
        sql=f"SELECT sumIf(amount, stage = 'CUSTOMER') FROM {PIPELINE} WHERE currency = 'USD' AND {Q3.sql()}",
        metrics=(M(("won_amount",), Q3, filters=USD),),
    ),
    P(
        id="pipeline_by_stage_usd_q3",
        text=(
            "For the US dollar opportunities created in the third quarter of 2026, what is the total amount "
            "at each pipeline stage today?"
        ),
        shape="table",
        unit="USD",
        label_name="stage",
        truth=lambda w: by(w.rows("twenty_pipeline", date=Q3, currency="USD"), "stage", "amount"),
        sql=(
            f"SELECT stage, sum(amount) FROM {PIPELINE} WHERE currency = 'USD' AND {Q3.sql()} GROUP BY "
            "stage ORDER BY 1"
        ),
        metrics=(M(("pipeline_amount",), Q3, ("stage",), USD, label="stage"),),
    ),
    P(
        id="opportunities_by_campaign_q3",
        text=(
            "How many opportunities created in the third quarter of 2026 does each campaign key account for? "
            "Include those with no campaign key."
        ),
        shape="table",
        unit="count",
        label_name="campaign key",
        truth=lambda w: count_by(w.rows("twenty_pipeline", date=Q3), "campaign_key"),
        sql=f"SELECT campaign_key, count() FROM {PIPELINE} WHERE {Q3.sql()} GROUP BY campaign_key ORDER BY 1",
        metrics=(M(("opportunities",), Q3, ("campaign_key",), label="campaign_key"),),
    ),
    P(
        id="unattributed_pipeline_usd_q3",
        text=(
            "What US dollar amount of the opportunities created in the third quarter of 2026 has no "
            "attribution at all?"
        ),
        shape="scalar",
        unit="USD",
        truth=lambda w: total(w.rows("twenty_pipeline", date=Q3, currency="USD", attributed=False), "amount"),
        sql=f"SELECT sum(amount) FROM {PIPELINE} WHERE currency = 'USD' AND NOT attributed AND {Q3.sql()}",
        metrics=(M(("pipeline_amount",), Q3, filters={"attributed": False, **USD}),),
    ),
    P(
        id="top_company_pipeline_usd_q3",
        text=(
            "Which company has the largest US dollar pipeline among opportunities created in the third "
            "quarter of 2026?"
        ),
        shape="label",
        unit="",
        label_name="company name",
        truth=lambda w: first(
            by(w.rows("twenty_pipeline", date=Q3, currency="USD"), "company_name", "amount")
        ),
        sql=(
            f"SELECT company_name FROM {PIPELINE} WHERE currency = 'USD' AND {Q3.sql()} "
            "GROUP BY company_name ORDER BY sum(amount) DESC LIMIT 1"
        ),
        metrics=(M(("pipeline_amount",), Q3, ("company_name",), USD, label="company_name"),),
    ),
    P(
        id="win_rate_q3",
        text=(
            "What share of the opportunities created in the third quarter of 2026 have been won, as a "
            "percentage?"
        ),
        shape="scalar",
        unit="percent",
        truth=lambda w: (
            100
            * (
                ratio(
                    len(w.rows("twenty_pipeline", date=Q3, stage="CUSTOMER")),
                    len(w.rows("twenty_pipeline", date=Q3)),
                )
                or 0
            )
        ),
        sql=f"SELECT 100 * countIf(stage = 'CUSTOMER') / count() FROM {PIPELINE} WHERE {Q3.sql()}",
        metrics=(M(("won_opportunities", "opportunities"), Q3, divide_by="opportunities", scale=100),),
    ),
    P(
        id="crm_tasks_sep",
        text="How many tasks were created in the CRM in September 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: distinct(w.rows("twenty_activity", date=SEP, kind="task"), "activity_id"),
        sql=f"SELECT uniqExactIf(activity_id, kind = 'task') FROM marts.twenty_activity WHERE {SEP.sql()}",
        naive_sql=f"SELECT countIf(kind = 'task') FROM marts.twenty_activity WHERE {SEP.sql()}",
        metrics=(M(("crm_tasks",), SEP),),
        tags=("pitfall", "grain_refused"),
    ),
)

QUESTIONS = REVENUE_QUESTIONS + PIPELINE_QUESTIONS
