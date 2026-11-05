-- Meta Ads campaign-level insights (DESIGN.md §4): one row per account, campaign and day.
-- Applied by omw.pipelines.meta_ads after each successful load. SQL SECURITY DEFINER keeps raw invisible to the agent.
CREATE OR REPLACE VIEW marts.meta_ads_campaigns (
    source LowCardinality(String) COMMENT 'Always meta_ads',
    report LowCardinality(String) COMMENT 'Report grain, always campaigns: one row per account, campaign and day. Never combine with the ads grain',
    account_id LowCardinality(String) COMMENT 'Meta ad account id without the act_ prefix',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the ad account. Spend is in this currency',
    date Date COMMENT 'Day in the ad account time zone. Meta restates up to 28 days back: the daily sync re-pulls 7 days, the weekly one 28',
    campaign_id String COMMENT 'Meta campaign id',
    campaign_name String COMMENT 'Campaign name at the time of the pull',
    impressions UInt64 COMMENT 'Times an ad of the campaign was on screen',
    clicks UInt64 COMMENT 'All clicks on the ad, including likes and expands, not only link clicks',
    reach UInt64 COMMENT 'People reached. Not additive across days or campaigns: a person counts once per row',
    spend Decimal(38, 9) COMMENT 'Amount spent in the account currency',
    frequency Float64 COMMENT 'impressions / reach as Meta computes it. A rate: never sum it',
    ctr Float64 COMMENT 'clicks / impressions in percent as Meta reports it. A rate: never sum it',
    cpc Float64 COMMENT 'Cost per click in the account currency. A rate: recompute from spend and clicks',
    cpm Float64 COMMENT 'Cost per 1000 impressions. A rate: recompute from spend and impressions',
    actions String COMMENT 'JSON list of {action_type, value} with the 7d_click/1d_view attribution. Conversions live here (purchase, lead, ...)',
    action_values String COMMENT 'JSON list of {action_type, value} with the monetary value of actions, account currency'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, account_id, currency, date, campaign_id, campaign_name,
       toUInt64(impressions) AS impressions, toUInt64(clicks) AS clicks, toUInt64(reach) AS reach,
       spend, frequency, ctr, cpc, cpm, actions, action_values
FROM raw.meta_ads___insights_campaigns;
