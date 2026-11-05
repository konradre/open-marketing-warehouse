-- Meta Ads ad-level insights (DESIGN.md §4): one row per account, ad and day, for live ad sets only.
-- Applied by omw.pipelines.meta_ads after each successful load at level ad. SQL SECURITY DEFINER keeps raw invisible.
CREATE OR REPLACE VIEW marts.meta_ads_ads (
    source LowCardinality(String) COMMENT 'Always meta_ads',
    report LowCardinality(String) COMMENT 'Report grain, always ads: one row per account, ad and day, pulled for ad sets that were ACTIVE at pull time. Never combine with the campaigns grain',
    account_id LowCardinality(String) COMMENT 'Meta ad account id without the act_ prefix',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the ad account. Spend is in this currency',
    date Date COMMENT 'Day in the ad account time zone. Meta restates up to 28 days back: the daily sync re-pulls 7 days, the weekly one 28',
    campaign_id String COMMENT 'Meta campaign id',
    campaign_name String COMMENT 'Campaign name at the time of the pull',
    adset_id String COMMENT 'Meta ad set id',
    adset_name String COMMENT 'Ad set name at the time of the pull',
    ad_id String COMMENT 'Meta ad id',
    ad_name String COMMENT 'Ad name at the time of the pull',
    impressions UInt64 COMMENT 'Times the ad was on screen',
    clicks UInt64 COMMENT 'All clicks on the ad, including likes and expands, not only link clicks',
    reach UInt64 COMMENT 'People reached. Not additive across days or ads: a person counts once per row',
    spend Decimal(38, 9) COMMENT 'Amount spent in the account currency',
    frequency Float64 COMMENT 'impressions / reach as Meta computes it. A rate: never sum it',
    ctr Float64 COMMENT 'clicks / impressions in percent as Meta reports it. A rate: never sum it',
    cpc Float64 COMMENT 'Cost per click in the account currency. A rate: recompute from spend and clicks',
    cpm Float64 COMMENT 'Cost per 1000 impressions. A rate: recompute from spend and impressions',
    actions String COMMENT 'JSON list of {action_type, value} with the 7d_click/1d_view attribution. Conversions live here',
    action_values String COMMENT 'JSON list of {action_type, value} with the monetary value of actions, account currency'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, account_id, currency, date, campaign_id, campaign_name, adset_id, adset_name, ad_id, ad_name,
       toUInt64(impressions) AS impressions, toUInt64(clicks) AS clicks, toUInt64(reach) AS reach,
       spend, frequency, ctr, cpc, cpm, actions, action_values
FROM raw.meta_ads___insights_ads;
