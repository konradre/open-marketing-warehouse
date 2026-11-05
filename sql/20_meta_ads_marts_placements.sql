-- Meta Ads campaign insights broken down by placement (DESIGN.md §4): a breakdown grain of marts.meta_ads_campaigns.
-- Applied by omw.pipelines.meta_ads after each load with --breakdown placements. SQL SECURITY DEFINER keeps raw invisible.
CREATE OR REPLACE VIEW marts.meta_ads_placements (
    source LowCardinality(String) COMMENT 'Always meta_ads',
    report LowCardinality(String) COMMENT 'Report grain, always placements: one row per account, campaign, placement and day. Sums to the campaigns grain on spend, impressions and clicks (reach does not). Never combine grains',
    account_id LowCardinality(String) COMMENT 'Meta ad account id without the act_ prefix',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the ad account. Spend is in this currency',
    date Date COMMENT 'Day in the ad account time zone. Meta restates up to 28 days back: the daily sync re-pulls 7 days, the weekly one 28',
    campaign_id String COMMENT 'Meta campaign id',
    campaign_name String COMMENT 'Campaign name at the time of the pull',
    publisher_platform LowCardinality(String) COMMENT 'facebook, instagram, audience_network or messenger',
    platform_position LowCardinality(String) COMMENT 'Position on the platform, for example feed, instagram_stories, instagram_reels or an_classic',
    impressions UInt64 COMMENT 'Times an ad of the campaign was on screen in this segment',
    clicks UInt64 COMMENT 'All clicks on the ad in this segment, not only link clicks',
    reach UInt64 COMMENT 'People reached in this segment. Not additive: a person counts once per row',
    spend Decimal(38, 9) COMMENT 'Amount spent in the account currency in this segment',
    frequency Float64 COMMENT 'impressions / reach as Meta computes it. A rate: never sum it',
    ctr Float64 COMMENT 'clicks / impressions in percent as Meta reports it. A rate: never sum it',
    cpc Float64 COMMENT 'Cost per click in the account currency. A rate: recompute from spend and clicks',
    cpm Float64 COMMENT 'Cost per 1000 impressions. A rate: recompute from spend and impressions',
    actions String COMMENT 'JSON list of {action_type, value} with the 7d_click/1d_view attribution',
    action_values String COMMENT 'JSON list of {action_type, value} with the monetary value of actions, account currency'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, account_id, currency, date, campaign_id, campaign_name, publisher_platform, platform_position,
       toUInt64(impressions) AS impressions, toUInt64(clicks) AS clicks, toUInt64(reach) AS reach,
       spend, frequency, ctr, cpc, cpm, actions, action_values
FROM raw.meta_ads___insights_placements;
