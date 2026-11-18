"""Rates: CTR, CPC, CPM, position. Each must be recomputed from its additive parts at the grain asked, so
the face-value query (average the rate column) is the classic way to a wrong number."""

from __future__ import annotations

from functools import partial

from omw.eval.generate.web import SITE
from omw.eval.questions.base import MetricStep as M
from omw.eval.questions.base import Question
from omw.eval.truth import ad_rows, by, divide, ratio, total
from omw.eval.world import AUG, LAST_7, SEP, Row, World

Q = partial(Question, category="rates")
USD = {"currency": "USD"}
WEB = {"search_type": "web"}
META = "marts.meta_ads_campaigns"
GOOGLE = "marts.google_ads_campaigns"
LINKEDIN = "marts.linkedin_ads_campaigns"
CORE = "marts.search_console_core"


def _rate(rows: list[Row], top: str, bottom: str, scale: float = 1.0) -> float | None:
    value = ratio(total(rows, top), total(rows, bottom))
    return None if value is None else scale * value


def _position(world: World, **where: object) -> float | None:
    rows = world.rows("search_console_core", **where)
    return ratio(sum(r["position"] * r["impressions"] for r in rows), total(rows, "impressions"))


def _blended_cpc(world: World) -> float | None:
    rows = ad_rows(world, SEP, "USD")
    return ratio(total(rows, "spend"), total(rows, "clicks"))


QUESTIONS: tuple[Question, ...] = (
    Q(
        id="meta_ctr_sep",
        text="What was the click-through rate of our Meta Ads in September 2026, as a percentage?",
        shape="scalar",
        unit="percent",
        truth=lambda w: _rate(w.rows("meta_ads_campaigns", date=SEP), "clicks", "impressions", 100),
        sql=f"SELECT 100 * sum(clicks) / sum(impressions) FROM {META} WHERE {SEP.sql()}",
        naive_sql=f"SELECT avg(ctr) FROM {META} WHERE {SEP.sql()}",
        metrics=(M(("meta_ctr",), SEP, scale=100),),
        tags=("pitfall",),
    ),
    Q(
        id="meta_cpc_sep",
        text="What was our cost per click on Meta Ads in September 2026?",
        shape="scalar",
        unit="USD",
        truth=lambda w: _rate(w.rows("meta_ads_campaigns", date=SEP), "spend", "clicks"),
        sql=f"SELECT sum(spend) / sum(clicks) FROM {META} WHERE {SEP.sql()}",
        naive_sql=f"SELECT avg(cpc) FROM {META} WHERE {SEP.sql()}",
        metrics=(M(("meta_cpc",), SEP, filters=USD),),
        tags=("pitfall",),
    ),
    Q(
        id="meta_ctr_by_campaign_sep",
        text="What was the click-through rate of each Meta campaign in September 2026, as a percentage?",
        shape="table",
        unit="percent",
        label_name="campaign name",
        truth=lambda w: divide(
            by(w.rows("meta_ads_campaigns", date=SEP), "campaign_name", "clicks"),
            by(w.rows("meta_ads_campaigns", date=SEP), "campaign_name", "impressions"),
            100,
        ),
        sql=(
            f"SELECT campaign_name, 100 * sum(clicks) / sum(impressions) FROM {META} WHERE {SEP.sql()} "
            "GROUP BY campaign_name ORDER BY 1"
        ),
        naive_sql=(
            f"SELECT campaign_name, avg(ctr) FROM {META} WHERE {SEP.sql()} GROUP BY campaign_name ORDER BY 1"
        ),
        metrics=(M(("meta_ctr",), SEP, ("campaign_name",), label="campaign_name", scale=100),),
        tags=("pitfall",),
    ),
    Q(
        id="meta_cpm_sep",
        text="What was the CPM (cost per thousand impressions) of our Meta Ads in September 2026?",
        shape="scalar",
        unit="USD",
        truth=lambda w: _rate(w.rows("meta_ads_campaigns", date=SEP), "spend", "impressions", 1000),
        sql=f"SELECT 1000 * sum(spend) / sum(impressions) FROM {META} WHERE {SEP.sql()}",
        naive_sql=f"SELECT avg(cpm) FROM {META} WHERE {SEP.sql()}",
        metrics=(
            M(("meta_spend", "meta_impressions"), SEP, filters=USD, divide_by="meta_impressions", scale=1000),
        ),
        tags=("pitfall",),
    ),
    Q(
        id="google_cpc_by_campaign_aug",
        text=(
            "What was the cost per click of each campaign in the US dollar Google Ads account in August 2026?"
        ),
        shape="table",
        unit="USD",
        label_name="campaign name",
        truth=lambda w: divide(
            by(w.rows("google_ads_campaigns", date=AUG, currency="USD"), "campaign_name", "cost"),
            by(w.rows("google_ads_campaigns", date=AUG, currency="USD"), "campaign_name", "clicks"),
        ),
        sql=(
            f"SELECT campaign_name, sum(cost) / sum(clicks) FROM {GOOGLE} WHERE currency = 'USD' AND "
            f"{AUG.sql()} "
            "GROUP BY campaign_name ORDER BY 1"
        ),
        naive_sql=(
            "SELECT campaign_name, avg(cost / clicks) FROM marts.google_ads_campaigns "
            f"WHERE currency = 'USD' AND clicks > 0 AND {AUG.sql()} GROUP BY campaign_name ORDER BY 1"
        ),
        metrics=(M(("google_cpc",), AUG, ("campaign_name",), USD, label="campaign_name"),),
        tags=("pitfall",),
    ),
    Q(
        id="google_cpc_brand_l7",
        text="What was the cost per click of the Google Ads campaign 'Brand - Search' over the last 7 days?",
        shape="scalar",
        unit="USD",
        truth=lambda w: _rate(
            w.rows("google_ads_campaigns", date=LAST_7, campaign_name="Brand - Search"), "cost", "clicks"
        ),
        sql=(
            f"SELECT sum(cost) / sum(clicks) FROM {GOOGLE} "
            f"WHERE campaign_name = 'Brand - Search' AND {LAST_7.sql()}"
        ),
        metrics=(M(("google_cpc",), "last_7_days", filters={"campaign_name": "Brand - Search", **USD}),),
    ),
    Q(
        id="linkedin_cpc_last_month",
        text="What was our LinkedIn Ads cost per click last month?",
        shape="scalar",
        unit="USD",
        truth=lambda w: _rate(w.rows("linkedin_ads_campaigns", date=SEP), "cost", "clicks"),
        sql=f"SELECT sum(cost) / sum(clicks) FROM {LINKEDIN} WHERE {SEP.sql()}",
        metrics=(M(("linkedin_cpc",), "last_month", filters=USD),),
    ),
    Q(
        id="linkedin_ctr_sep",
        text="What was the click-through rate of our LinkedIn Ads in September 2026, as a percentage?",
        shape="scalar",
        unit="percent",
        truth=lambda w: _rate(w.rows("linkedin_ads_campaigns", date=SEP), "clicks", "impressions", 100),
        sql=f"SELECT 100 * sum(clicks) / sum(impressions) FROM {LINKEDIN} WHERE {SEP.sql()}",
        metrics=(
            M(("linkedin_clicks", "linkedin_impressions"), SEP, divide_by="linkedin_impressions", scale=100),
        ),
    ),
    Q(
        id="google_conversion_rate_aug",
        text=(
            "What share of clicks converted in the US dollar Google Ads account in August 2026 "
            "(conversions per click, as a percentage)?"
        ),
        shape="scalar",
        unit="percent",
        truth=lambda w: _rate(
            w.rows("google_ads_campaigns", date=AUG, currency="USD"), "conversions", "clicks", 100
        ),
        sql=(
            f"SELECT 100 * sum(conversions) / sum(clicks) FROM {GOOGLE} WHERE currency = 'USD' AND "
            f"{AUG.sql()}"
        ),
        metrics=(
            M(
                ("google_conversions", "google_clicks"),
                AUG,
                filters=USD,
                divide_by="google_clicks",
                scale=100,
            ),
        ),
    ),
    Q(
        id="blended_cpc_usd_sep",
        text=(
            "What was our blended cost per click across Meta, Google Ads and LinkedIn in September 2026, "
            "for the US dollar accounts?"
        ),
        shape="scalar",
        unit="USD",
        truth=_blended_cpc,
        sql=(
            "SELECT sum(spend) / sum(clicks) FROM ("
            f"SELECT sum(spend) AS spend, sum(clicks) AS clicks FROM {META} WHERE currency = 'USD' AND "
            f"{SEP.sql()} "
            f"UNION ALL SELECT sum(cost), sum(clicks) FROM {GOOGLE} WHERE currency = 'USD' AND {SEP.sql()} "
            f"UNION ALL SELECT sum(cost), sum(clicks) FROM {LINKEDIN} WHERE currency = 'USD' AND {SEP.sql()})"
        ),
        naive_sql=(
            "SELECT avg(cpc) FROM ("
            f"SELECT sum(spend) / sum(clicks) AS cpc FROM {META} WHERE currency = 'USD' AND {SEP.sql()} "
            f"UNION ALL SELECT sum(cost) / sum(clicks) FROM {GOOGLE} WHERE currency = 'USD' AND {SEP.sql()} "
            f"UNION ALL SELECT sum(cost) / sum(clicks) FROM {LINKEDIN} WHERE currency = 'USD' AND "
            f"{SEP.sql()})"
        ),
        metrics=(M(("blended_cpc",), SEP, filters=USD),),
        tags=("pitfall", "cross_source"),
    ),
    Q(
        id="search_ctr_l30",
        text=(
            "What was our organic click-through rate in Google web search over the last 30 days, as a "
            "percentage?"
        ),
        shape="scalar",
        unit="percent",
        truth=lambda w: _rate(
            w.rows("search_console_core", date=SEP, search_type="web"), "clicks", "impressions", 100
        ),
        sql=(
            f"SELECT 100 * sum(clicks) / sum(impressions) FROM {CORE} WHERE search_type = 'web' AND "
            f"{SEP.sql()}"
        ),
        naive_sql=f"SELECT 100 * avg(ctr) FROM {CORE} WHERE search_type = 'web' AND {SEP.sql()}",
        metrics=(M(("search_ctr",), "last_30_days", filters=WEB, scale=100),),
        tags=("pitfall",),
    ),
    Q(
        id="search_ctr_by_type_sep",
        text=(
            "What was our Google Search click-through rate in September 2026 for each search type, as a "
            "percentage?"
        ),
        shape="table",
        unit="percent",
        label_name="search type",
        truth=lambda w: divide(
            by(w.rows("search_console_core", date=SEP), "search_type", "clicks"),
            by(w.rows("search_console_core", date=SEP), "search_type", "impressions"),
            100,
        ),
        sql=(
            f"SELECT search_type, 100 * sum(clicks) / sum(impressions) FROM {CORE} WHERE {SEP.sql()} "
            "GROUP BY search_type ORDER BY 1"
        ),
        metrics=(M(("search_ctr",), SEP, ("search_type",), label="search_type", scale=100),),
    ),
    Q(
        id="search_position_sep_web",
        text="What was our average position in Google web search in September 2026?",
        shape="scalar",
        unit="position, 1 = top",
        truth=lambda w: _position(w, date=SEP, search_type="web"),
        sql=(
            f"SELECT sum(position * impressions) / sum(impressions) FROM {CORE} "
            f"WHERE search_type = 'web' AND {SEP.sql()}"
        ),
        naive_sql=(
            "SELECT avg(position) FROM marts.search_console_queries WHERE search_type = 'web' AND "
            f"{SEP.sql()}"
        ),
        metrics=(M(("search_position",), SEP, filters={"site_url": SITE, **WEB}),),
        tags=("pitfall",),
    ),
)
