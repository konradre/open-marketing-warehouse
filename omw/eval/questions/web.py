"""Organic search (Search Console) and site analytics (GA4)."""

from __future__ import annotations

from functools import partial

from omw.eval.generate.web import PROPERTY
from omw.eval.questions.base import MetricStep as M
from omw.eval.questions.base import Question
from omw.eval.truth import by, first, total
from omw.eval.world import AUG, LAST_7, Q3, SEP, Period

S = partial(Question, category="search")
G = partial(Question, category="ga4")
WEB = {"search_type": "web"}
CORE = "marts.search_console_core"
QUERIES = "marts.search_console_queries"
DAY = Period(SEP.start.replace(day=15), SEP.start.replace(day=15))
STALE = ("stale", "freshness", "last sync")

SEARCH: tuple[Question, ...] = (
    S(
        id="search_clicks_sep_web",
        text="How many clicks did we get from Google web search in September 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: total(w.rows("search_console_core", date=SEP, search_type="web"), "clicks"),
        sql=f"SELECT sum(clicks) FROM {CORE} WHERE search_type = 'web' AND {SEP.sql()}",
        naive_sql=f"SELECT sum(clicks) FROM {QUERIES} WHERE search_type = 'web' AND {SEP.sql()}",
        metrics=(M(("search_clicks",), SEP, filters=WEB),),
        tags=("pitfall",),
    ),
    S(
        id="search_impressions_aug_web",
        text="How many impressions did we get in Google web search in August 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: total(w.rows("search_console_core", date=AUG, search_type="web"), "impressions"),
        sql=f"SELECT sum(impressions) FROM {CORE} WHERE search_type = 'web' AND {AUG.sql()}",
        metrics=(M(("search_impressions",), AUG, filters=WEB),),
    ),
    S(
        id="search_clicks_by_type_sep",
        text="How many Google Search clicks did each search type bring in September 2026?",
        shape="table",
        unit="count",
        label_name="search type",
        truth=lambda w: by(w.rows("search_console_core", date=SEP), "search_type", "clicks"),
        sql=f"SELECT search_type, sum(clicks) FROM {CORE} WHERE {SEP.sql()} GROUP BY search_type ORDER BY 1",
        metrics=(M(("search_clicks",), SEP, ("search_type",), label="search_type"),),
    ),
    S(
        id="top_queries_sep",
        text="Which five search queries brought the most clicks from Google web search in September 2026?",
        shape="table",
        unit="clicks",
        label_name="query",
        top=5,
        truth=lambda w: by(w.rows("search_console_queries", date=SEP, search_type="web"), "query", "clicks"),
        sql=(
            f"SELECT query, sum(clicks) AS clicks FROM {QUERIES} WHERE search_type = 'web' AND {SEP.sql()} "
            "GROUP BY query ORDER BY clicks DESC LIMIT 5"
        ),
        metrics=(M(("query_clicks",), SEP, ("query",), WEB, label="query"),),
    ),
    S(
        id="query_clicks_widgets_pricing_q3",
        text=(
            "How many clicks did the query 'widgets pricing' get in Google web search in the third quarter "
            "of 2026 (July to September)?"
        ),
        shape="scalar",
        unit="count",
        truth=lambda w: total(
            w.rows("search_console_queries", date=Q3, search_type="web", query="widgets pricing"), "clicks"
        ),
        sql=(
            f"SELECT sum(clicks) FROM {QUERIES} "
            f"WHERE search_type = 'web' AND query = 'widgets pricing' AND {Q3.sql()}"
        ),
        metrics=(M(("query_clicks",), Q3, filters={"query": "widgets pricing", **WEB}),),
    ),
    S(
        id="top_pages_sep",
        text="Which three pages got the most clicks from Google web search in September 2026?",
        shape="table",
        unit="clicks",
        label_name="page URL",
        top=3,
        truth=lambda w: by(w.rows("search_console_pages", date=SEP, search_type="web"), "page", "clicks"),
        sql=(
            "SELECT page, sum(clicks) AS clicks FROM marts.search_console_pages "
            f"WHERE search_type = 'web' AND {SEP.sql()} GROUP BY page ORDER BY clicks DESC LIMIT 3"
        ),
        metrics=(M(("page_clicks",), SEP, ("page",), WEB, label="page"),),
    ),
    S(
        id="query_clicks_total_sep",
        text="How many Google web search clicks in September 2026 can be attributed to a named search query?",
        shape="scalar",
        unit="count",
        truth=lambda w: total(w.rows("search_console_queries", date=SEP, search_type="web"), "clicks"),
        caveat_terms=("anonym", "withh", "undercount", "lower than"),
        sql=f"SELECT sum(clicks) FROM {QUERIES} WHERE search_type = 'web' AND {SEP.sql()}",
        sql_caveat="The queries report undercounts total clicks: Google withholds anonymised queries.",
        metrics=(M(("query_clicks",), SEP, filters=WEB),),
    ),
    S(
        id="search_clicks_by_device_sep",
        text="How many Google web search clicks came from each device class in September 2026?",
        shape="table",
        unit="count",
        label_name="device class",
        truth=lambda w: by(w.rows("search_console_devices", date=SEP, search_type="web"), "device", "clicks"),
        sql=(
            "SELECT device, sum(clicks) FROM marts.search_console_devices "
            f"WHERE search_type = 'web' AND {SEP.sql()} GROUP BY device ORDER BY 1"
        ),
        metrics=(M(("search_device_clicks",), SEP, ("device",), {"search_type": "web"}, label="device"),),
    ),
    S(
        id="top_search_country_aug",
        text="Which country brought the most Google web search clicks in August 2026?",
        shape="label",
        unit="",
        label_name="country code",
        truth=lambda w: first(
            by(w.rows("search_console_countries", date=AUG, search_type="web"), "country", "clicks")
        ),
        sql=(
            "SELECT country FROM marts.search_console_countries "
            f"WHERE search_type = 'web' AND {AUG.sql()} GROUP BY country ORDER BY sum(clicks) DESC LIMIT 1"
        ),
        metrics=(M(("search_country_clicks",), AUG, ("country",), {"search_type": "web"}, label="country"),),
    ),
    S(
        id="top_page_for_query_sep",
        text=(
            "Which page got the most clicks for the query 'buy widgets online' in Google web search "
            "in September 2026?"
        ),
        shape="label",
        unit="",
        label_name="page URL",
        truth=lambda w: first(
            by(
                w.rows("search_console_query_page", date=SEP, search_type="web", query="buy widgets online"),
                "page",
                "clicks",
            )
        ),
        sql=(
            "SELECT page FROM marts.search_console_query_page "
            f"WHERE search_type = 'web' AND query = 'buy widgets online' AND {SEP.sql()} "
            "GROUP BY page ORDER BY sum(clicks) DESC LIMIT 1"
        ),
        metrics=(
            M(
                ("search_query_page_clicks",),
                SEP,
                ("page",),
                {"search_type": "web", "query": "buy widgets online"},
                label="page",
            ),
        ),
    ),
)

GA4: tuple[Question, ...] = (
    G(
        id="sessions_sep",
        text="How many sessions did the website get in September 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: total(w.rows("ga4_core", date=SEP), "sessions"),
        sql=f"SELECT sum(sessions) FROM marts.ga4_core WHERE {SEP.sql()}",
        naive_sql=(
            "SELECT sum(sessions) FROM (SELECT sessions FROM marts.ga4_core "
            f"WHERE {SEP.sql()} UNION ALL SELECT sessions FROM marts.ga4_channels WHERE {SEP.sql()})"
        ),
        metrics=(M(("sessions",), SEP),),
        tags=("pitfall",),
    ),
    G(
        id="new_users_aug",
        text="How many new users did the website get in August 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: total(w.rows("ga4_core", date=AUG), "new_users"),
        sql=f"SELECT sum(new_users) FROM marts.ga4_core WHERE {AUG.sql()}",
        metrics=(M(("new_users",), AUG),),
    ),
    G(
        id="key_events_l7",
        text="How many GA4 key events did the website record over the last 7 days?",
        shape="scalar",
        unit="events",
        truth=lambda w: total(w.rows("ga4_core", date=LAST_7), "key_events"),
        caveat_terms=STALE,
        sql=f"SELECT sum(key_events) FROM marts.ga4_core WHERE {LAST_7.sql()}",
        sql_caveat=(
            "ops.freshness reports ga4 as failing: its last sync is past the budget, so this may be stale."
        ),
        metrics=(M(("key_events",), "last_7_days"),),
    ),
    G(
        id="sessions_by_channel_sep",
        text="How many sessions came from each GA4 default channel group in September 2026?",
        shape="table",
        unit="count",
        label_name="channel group",
        truth=lambda w: by(w.rows("ga4_channels", date=SEP), "channel_group", "sessions"),
        sql=(
            f"SELECT channel_group, sum(sessions) FROM marts.ga4_channels WHERE {SEP.sql()} "
            "GROUP BY channel_group ORDER BY 1"
        ),
        metrics=(M(("channel_sessions",), SEP, ("channel_group",), label="channel_group"),),
    ),
    G(
        id="sessions_paid_social_q3",
        text=(
            "How many sessions came from the Paid Social channel in the third quarter of 2026 (July to "
            "September)?"
        ),
        shape="scalar",
        unit="count",
        truth=lambda w: total(w.rows("ga4_channels", date=Q3, channel_group="Paid Social"), "sessions"),
        sql=(
            f"SELECT sum(sessions) FROM marts.ga4_channels WHERE channel_group = 'Paid Social' AND {Q3.sql()}"
        ),
        metrics=(M(("channel_sessions",), Q3, filters={"channel_group": "Paid Social"}),),
    ),
    G(
        id="sessions_campaign_brand_sep",
        text=(
            "How many website sessions did GA4 attribute to the campaign 'Brand - Search' in September 2026?"
        ),
        shape="scalar",
        unit="count",
        truth=lambda w: total(w.rows("ga4_campaigns", date=SEP, campaign="Brand - Search"), "sessions"),
        sql=(
            f"SELECT sum(sessions) FROM marts.ga4_campaigns WHERE campaign = 'Brand - Search' AND {SEP.sql()}"
        ),
        metrics=(M(("campaign_sessions",), SEP, filters={"campaign": "Brand - Search"}),),
    ),
    G(
        id="other_bucket_sessions_sep",
        text="How many September 2026 sessions did GA4 fold into its (other) channel bucket?",
        shape="scalar",
        unit="count",
        truth=lambda w: total(w.rows("ga4_channels", date=SEP, is_other=True), "sessions"),
        sql=f"SELECT sum(sessions) FROM marts.ga4_channels WHERE is_other AND {SEP.sql()}",
        metrics=(M(("channel_sessions",), SEP, filters={"is_other": True}),),
    ),
    G(
        id="users_on_day",
        text="How many users were active on the website on 15 September 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: total(w.rows("ga4_core", date=DAY), "total_users"),
        sql=f"SELECT sum(total_users) FROM marts.ga4_core WHERE {DAY.sql()}",
        metrics=(M(("users",), DAY, ("date", "property_id"), {"property_id": PROPERTY}),),
    ),
    G(
        id="top_landing_pages_sep",
        text="Which three landing pages started the most sessions in September 2026?",
        shape="table",
        unit="sessions",
        label_name="landing page path",
        top=3,
        truth=lambda w: by(w.rows("ga4_landing_pages", date=SEP), "landing_page", "sessions"),
        sql=(
            f"SELECT landing_page, sum(sessions) AS sessions FROM marts.ga4_landing_pages WHERE {SEP.sql()} "
            "GROUP BY landing_page ORDER BY sessions DESC LIMIT 3"
        ),
        metrics=(M(("landing_page_sessions",), SEP, ("landing_page",), label="landing_page"),),
    ),
)

QUESTIONS = SEARCH + GA4
