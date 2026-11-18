"""Spend and delivery on one ad platform at a time, across every report grain."""

from __future__ import annotations

from functools import partial

from omw.eval.generate.campaigns import GOOGLE_USD
from omw.eval.questions.base import MetricStep as M
from omw.eval.questions.base import Question
from omw.eval.truth import by, first, total
from omw.eval.world import AUG, JUL, LAST_7, Q3, SEP, Period

Q = partial(Question, category="spend")
USD = {"currency": "USD"}
META = "marts.meta_ads_campaigns"
GOOGLE = "marts.google_ads_campaigns"
LINKEDIN = "marts.linkedin_ads_campaigns"
RESTATED = ("restat", "may still change", "may change", "provisional")
DAY = Period(SEP.start.replace(day=15), SEP.start.replace(day=15))
META_GRAIN = ("date", "account_id", "currency", "campaign_id", "campaign_name", "campaign_key")

QUESTIONS: tuple[Question, ...] = (
    Q(
        id="meta_spend_sep",
        text="How much did we spend on Meta Ads in September 2026?",
        shape="scalar",
        unit="USD",
        truth=lambda w: total(w.rows("meta_ads_campaigns", date=SEP), "spend"),
        sql=f"SELECT sum(spend) FROM {META} WHERE {SEP.sql()}",
        metrics=(M(("meta_spend",), SEP, filters=USD),),
    ),
    Q(
        id="google_spend_usd_aug",
        text="What did the US dollar Google Ads account spend in August 2026?",
        shape="scalar",
        unit="USD",
        truth=lambda w: total(w.rows("google_ads_campaigns", date=AUG, currency="USD"), "cost"),
        sql=f"SELECT sum(cost) FROM {GOOGLE} WHERE currency = 'USD' AND {AUG.sql()}",
        metrics=(M(("google_spend",), AUG, filters=USD),),
    ),
    Q(
        id="google_spend_by_currency_sep",
        text="What was Google Ads spend in September 2026, for each account currency?",
        shape="table",
        unit="account currency",
        label_name="currency",
        truth=lambda w: by(w.rows("google_ads_campaigns", date=SEP), "currency", "cost"),
        sql=f"SELECT currency, sum(cost) FROM {GOOGLE} WHERE {SEP.sql()} GROUP BY currency ORDER BY currency",
        metrics=(M(("google_spend",), SEP, ("currency",), label="currency"),),
    ),
    Q(
        id="linkedin_spend_last_month",
        text="What was our LinkedIn Ads spend last month?",
        shape="scalar",
        unit="USD",
        truth=lambda w: total(w.rows("linkedin_ads_campaigns", date=SEP), "cost"),
        sql=f"SELECT sum(cost) FROM {LINKEDIN} WHERE {SEP.sql()}",
        metrics=(M(("linkedin_spend",), "last_month", filters=USD),),
    ),
    Q(
        id="meta_spend_by_campaign_aug",
        text="How much did each Meta campaign spend in August 2026?",
        shape="table",
        unit="USD",
        label_name="campaign name",
        truth=lambda w: by(w.rows("meta_ads_campaigns", date=AUG), "campaign_name", "spend"),
        sql=(
            f"SELECT campaign_name, sum(spend) FROM {META} WHERE {AUG.sql()} GROUP BY campaign_name ORDER "
            "BY 1"
        ),
        metrics=(M(("meta_spend",), AUG, ("campaign_name",), USD, label="campaign_name"),),
    ),
    Q(
        id="google_spend_by_campaign_l7",
        text="Google Ads spend by campaign over the last 7 days, for the US dollar account.",
        shape="table",
        unit="USD",
        label_name="campaign name",
        truth=lambda w: by(
            w.rows("google_ads_campaigns", date=LAST_7, currency="USD"), "campaign_name", "cost"
        ),
        sql=(
            f"SELECT campaign_name, sum(cost) FROM {GOOGLE} WHERE currency = 'USD' AND {LAST_7.sql()} "
            "GROUP BY campaign_name ORDER BY 1"
        ),
        metrics=(M(("google_spend",), "last_7_days", ("campaign_name",), USD, label="campaign_name"),),
    ),
    Q(
        id="meta_impressions_jul",
        text="How many impressions did Meta Ads deliver in July 2026?",
        shape="scalar",
        unit="count",
        truth=lambda w: total(w.rows("meta_ads_campaigns", date=JUL), "impressions"),
        sql=f"SELECT sum(impressions) FROM {META} WHERE {JUL.sql()}",
        metrics=(M(("meta_impressions",), JUL),),
    ),
    Q(
        id="google_clicks_by_channel_q3",
        text=(
            "How many Google Ads clicks did each channel type get in the third quarter of 2026 (July to "
            "September)?"
        ),
        shape="table",
        unit="count",
        label_name="channel type",
        truth=lambda w: by(w.rows("google_ads_campaigns", date=Q3), "channel_type", "clicks"),
        sql=(
            f"SELECT channel_type, sum(clicks) FROM {GOOGLE} WHERE {Q3.sql()} GROUP BY channel_type ORDER "
            "BY 1"
        ),
        metrics=(M(("google_clicks",), Q3, ("channel_type",), label="channel_type"),),
    ),
    Q(
        id="top_meta_campaign_sep",
        text="Which Meta campaign spent the most in September 2026?",
        shape="label",
        unit="",
        label_name="campaign name",
        truth=lambda w: first(by(w.rows("meta_ads_campaigns", date=SEP), "campaign_name", "spend")),
        sql=(
            f"SELECT campaign_name FROM {META} WHERE {SEP.sql()} GROUP BY campaign_name "
            "ORDER BY sum(spend) DESC LIMIT 1"
        ),
        metrics=(M(("meta_spend",), SEP, ("campaign_name",), USD, label="campaign_name"),),
    ),
    Q(
        id="meta_daily_spend_l7",
        text="What was Meta Ads spend on each of the last 7 days?",
        shape="table",
        unit="USD",
        label_name="day (YYYY-MM-DD)",
        truth=lambda w: by(w.rows("meta_ads_campaigns", date=LAST_7), "date", "spend"),
        caveat_terms=RESTATED,
        sql=f"SELECT date, sum(spend) FROM {META} WHERE {LAST_7.sql()} GROUP BY date ORDER BY date",
        sql_caveat="Meta restates spend for up to 28 days; these days may still change.",
        metrics=(M(("meta_spend",), "last_7_days", ("date",), USD, label="date"),),
    ),
    Q(
        id="google_conversions_aug_usd",
        text="How many conversions did the US dollar Google Ads account record in August 2026?",
        shape="scalar",
        unit="conversions, fractional",
        truth=lambda w: total(w.rows("google_ads_campaigns", date=AUG, currency="USD"), "conversions"),
        sql=f"SELECT sum(conversions) FROM {GOOGLE} WHERE currency = 'USD' AND {AUG.sql()}",
        metrics=(M(("google_conversions",), AUG, filters={"customer_id": GOOGLE_USD}),),
    ),
    Q(
        id="google_conversions_l7",
        text="How many Google Ads conversions were recorded over the last 7 days, across both accounts?",
        shape="scalar",
        unit="conversions, fractional",
        truth=lambda w: total(w.rows("google_ads_campaigns", date=LAST_7), "conversions"),
        caveat_terms=("30", "attribut", "restat", "may still change", "may change", "grow"),
        sql=f"SELECT sum(conversions) FROM {GOOGLE} WHERE {LAST_7.sql()}",
        sql_caveat="Google attributes conversions for up to 30 days after the click; recent days still grow.",
        metrics=(M(("google_conversions",), "last_7_days"),),
    ),
    Q(
        id="meta_spend_by_country_sep",
        text="How was September 2026 Meta spend split by country?",
        shape="table",
        unit="USD",
        label_name="country code",
        truth=lambda w: by(w.rows("meta_ads_countries", date=SEP), "country", "spend"),
        sql=(
            f"SELECT country, sum(spend) FROM marts.meta_ads_countries WHERE {SEP.sql()} GROUP BY country "
            "ORDER BY 1"
        ),
        metrics=(M(("meta_country_spend",), SEP, ("country",), USD, label="country"),),
    ),
    Q(
        id="meta_clicks_by_age_aug",
        text="How many Meta clicks came from each age bracket in August 2026?",
        shape="table",
        unit="count",
        label_name="age bracket",
        truth=lambda w: by(w.rows("meta_ads_age_gender", date=AUG), "age", "clicks"),
        sql=(
            f"SELECT age, sum(clicks) FROM marts.meta_ads_age_gender WHERE {AUG.sql()} GROUP BY age ORDER "
            "BY 1"
        ),
        metrics=(M(("meta_age_gender_clicks",), AUG, ("age",), label="age"),),
    ),
    Q(
        id="meta_spend_instagram_sep",
        text="How much of our September 2026 Meta spend went to Instagram placements?",
        shape="scalar",
        unit="USD",
        truth=lambda w: total(
            w.rows("meta_ads_placements", date=SEP, publisher_platform="instagram"), "spend"
        ),
        sql=(
            "SELECT sum(spend) FROM marts.meta_ads_placements "
            f"WHERE publisher_platform = 'instagram' AND {SEP.sql()}"
        ),
        metrics=(M(("meta_placement_spend",), SEP, filters={"publisher_platform": "instagram", **USD}),),
    ),
    Q(
        id="google_ad_group_spend_brand_sep",
        text=(
            "For the Google Ads campaign 'Brand - Search', how much did each ad group spend in September "
            "2026?"
        ),
        shape="table",
        unit="USD",
        label_name="ad group name",
        truth=lambda w: by(
            w.rows("google_ads_ad_groups", date=SEP, campaign_name="Brand - Search"), "ad_group_name", "cost"
        ),
        sql=(
            "SELECT ad_group_name, sum(cost) FROM marts.google_ads_ad_groups "
            f"WHERE campaign_name = 'Brand - Search' AND {SEP.sql()} GROUP BY ad_group_name ORDER BY 1"
        ),
        metrics=(
            M(
                ("google_ad_group_spend",),
                SEP,
                ("ad_group_name",),
                {"campaign_name": "Brand - Search", **USD},
                label="ad_group_name",
            ),
        ),
    ),
    Q(
        id="google_asset_group_spend_sep",
        text="How much did each Performance Max asset group spend in September 2026?",
        shape="table",
        unit="USD",
        label_name="asset group name",
        truth=lambda w: by(w.rows("google_ads_asset_groups", date=SEP), "asset_group_name", "cost"),
        sql=(
            f"SELECT asset_group_name, sum(cost) FROM marts.google_ads_asset_groups WHERE {SEP.sql()} "
            "GROUP BY asset_group_name ORDER BY 1"
        ),
        metrics=(
            M(("google_asset_group_spend",), SEP, ("asset_group_name",), USD, label="asset_group_name"),
        ),
    ),
    Q(
        id="linkedin_creative_clicks_gadgets_sep",
        text=(
            "For the LinkedIn campaign 'Gadgets Launch', how many clicks did each creative get in "
            "September 2026?"
        ),
        shape="table",
        unit="count",
        label_name="creative id",
        truth=lambda w: by(
            w.rows("linkedin_ads_creatives", date=SEP, campaign_name="Gadgets Launch"),
            "creative_id",
            "clicks",
        ),
        sql=(
            "SELECT creative_id, sum(clicks) FROM marts.linkedin_ads_creatives "
            f"WHERE campaign_name = 'Gadgets Launch' AND {SEP.sql()} GROUP BY creative_id ORDER BY 1"
        ),
        metrics=(
            M(
                ("linkedin_creative_clicks",),
                SEP,
                ("creative_id",),
                {"campaign_name": "Gadgets Launch"},
                label="creative_id",
            ),
        ),
    ),
    Q(
        id="google_top_search_terms_sep",
        text="Which five Google Ads search terms got the most clicks in September 2026?",
        shape="table",
        unit="clicks",
        label_name="search term",
        top=5,
        truth=lambda w: by(w.rows("google_ads_search_terms", date=SEP), "search_term", "clicks"),
        sql=(
            f"SELECT search_term, sum(clicks) AS clicks FROM marts.google_ads_search_terms WHERE {SEP.sql()} "
            "GROUP BY search_term ORDER BY clicks DESC LIMIT 5"
        ),
        metrics=(M(("google_search_term_clicks",), SEP, ("search_term",), label="search_term"),),
    ),
    Q(
        id="google_search_term_clicks_brand_sep",
        text=(
            "How many clicks do the search terms of the Google Ads campaign 'Brand - Search' add up to "
            "for September 2026?"
        ),
        shape="scalar",
        unit="count",
        truth=lambda w: total(
            w.rows("google_ads_search_terms", date=SEP, campaign_name="Brand - Search"), "clicks"
        ),
        caveat_terms=("withh", "undercount", "low-volume", "less than"),
        sql=(
            "SELECT sum(clicks) FROM marts.google_ads_search_terms "
            f"WHERE campaign_name = 'Brand - Search' AND {SEP.sql()}"
        ),
        sql_caveat="Search terms undercount the campaign: Google withholds low-volume terms.",
        metrics=(M(("google_search_term_clicks",), SEP, filters={"campaign_name": "Brand - Search"}),),
    ),
    Q(
        id="meta_reach_by_campaign_day",
        text="What was the reach of each Meta campaign on 15 September 2026?",
        shape="table",
        unit="people",
        label_name="campaign name",
        truth=lambda w: by(w.rows("meta_ads_campaigns", date=DAY), "campaign_name", "reach"),
        sql=(
            f"SELECT campaign_name, sum(reach) FROM {META} WHERE {DAY.sql()} GROUP BY campaign_name ORDER "
            "BY 1"
        ),
        metrics=(M(("meta_reach",), DAY, META_GRAIN, label="campaign_name"),),
    ),
    Q(
        id="google_spend_pmax_sep",
        text="How much did Performance Max campaigns spend on Google Ads in September 2026?",
        shape="scalar",
        unit="USD",
        truth=lambda w: total(
            w.rows("google_ads_campaigns", date=SEP, channel_type="PERFORMANCE_MAX"), "cost"
        ),
        sql=f"SELECT sum(cost) FROM {GOOGLE} WHERE channel_type = 'PERFORMANCE_MAX' AND {SEP.sql()}",
        metrics=(M(("google_spend",), SEP, filters={"channel_type": "PERFORMANCE_MAX", **USD}),),
    ),
    Q(
        id="meta_spend_by_ad_gadgets_sep",
        text="For the Meta campaign 'Gadgets Launch', how much did each ad spend in September 2026?",
        shape="table",
        unit="USD",
        label_name="ad name",
        truth=lambda w: by(
            w.rows("meta_ads_ads", date=SEP, campaign_name="Gadgets Launch"), "ad_name", "spend"
        ),
        sql=(
            "SELECT ad_name, sum(spend) FROM marts.meta_ads_ads "
            f"WHERE campaign_name = 'Gadgets Launch' AND {SEP.sql()} GROUP BY ad_name ORDER BY 1"
        ),
        tags=("not_in_catalog",),
    ),
    Q(
        id="meta_daily_budgets",
        text="What is the daily budget of each Meta campaign?",
        shape="table",
        unit="USD",
        label_name="campaign name",
        truth=lambda w: by(w.rows("meta_ads_campaign_list"), "campaign_name", "daily_budget"),
        sql="SELECT campaign_name, daily_budget FROM marts.meta_ads_campaign_list ORDER BY 1",
        tags=("not_in_catalog",),
    ),
)
