-- Google Ads marts (DESIGN.md §4): campaign, ad group and search term daily performance, cost in the account currency.
-- Applied by omw.pipelines.google_ads after each successful load. SQL SECURITY DEFINER keeps raw invisible to the agent.
-- cost_micros is Google's integer micro-amount: the views divide by 1 000 000 into a Decimal so no float rounding.

CREATE OR REPLACE VIEW marts.google_ads_campaigns (
    source LowCardinality(String) COMMENT 'Always google_ads',
    report LowCardinality(String) COMMENT 'Report grain, always campaigns: one row per customer, campaign and day. Never combine with the ad_groups grain',
    customer_id LowCardinality(String) COMMENT 'Google Ads customer (account) id, digits only',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the customer account. cost is in this currency',
    date Date COMMENT 'Day in the account time zone. Conversions are attributed for up to 30 days after the click, so the last 30 days are re-pulled daily and may change',
    campaign_id String COMMENT 'Google Ads campaign id',
    campaign_name String COMMENT 'Campaign name at the time of the pull',
    campaign_status LowCardinality(String) COMMENT 'ENABLED, PAUSED or UNKNOWN at pull time. REMOVED campaigns are not pulled',
    channel_type LowCardinality(String) COMMENT 'Advertising channel: SEARCH, DISPLAY, SHOPPING, VIDEO, PERFORMANCE_MAX, ...',
    impressions UInt64 COMMENT 'Times an ad of the campaign was shown',
    clicks UInt64 COMMENT 'Clicks on the ads',
    cost Decimal(18, 6) COMMENT 'Amount spent in the account currency (cost_micros / 1 000 000)',
    cost_micros Int64 COMMENT 'Amount spent in micro units of the account currency, as Google reports it',
    conversions Float64 COMMENT 'Conversions counted by the account conversion actions, fractional with modelled attribution. Depends on the account conversion setup',
    conversions_value Float64 COMMENT 'Value of the conversions in the account currency, as configured per conversion action'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, customer_id, currency, date, campaign_id, campaign_name, campaign_status, channel_type,
       toUInt64(impressions) AS impressions, toUInt64(clicks) AS clicks,
       toDecimal64(cost_micros, 6) / 1000000 AS cost, cost_micros, conversions, conversions_value
FROM raw.google_ads___performance_campaigns;

CREATE OR REPLACE VIEW marts.google_ads_ad_groups (
    source LowCardinality(String) COMMENT 'Always google_ads',
    report LowCardinality(String) COMMENT 'Report grain, always ad_groups: one row per customer, ad group and day. Sums to the campaigns grain. Never combine the two',
    customer_id LowCardinality(String) COMMENT 'Google Ads customer (account) id, digits only',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the customer account. cost is in this currency',
    date Date COMMENT 'Day in the account time zone. The last 30 days are re-pulled daily for conversion lag and may change',
    campaign_id String COMMENT 'Google Ads campaign id',
    campaign_name String COMMENT 'Campaign name at the time of the pull',
    ad_group_id String COMMENT 'Google Ads ad group id',
    ad_group_name String COMMENT 'Ad group name at the time of the pull',
    ad_group_status LowCardinality(String) COMMENT 'ENABLED, PAUSED or UNKNOWN at pull time',
    impressions UInt64 COMMENT 'Times an ad of the ad group was shown',
    clicks UInt64 COMMENT 'Clicks on the ads',
    cost Decimal(18, 6) COMMENT 'Amount spent in the account currency (cost_micros / 1 000 000)',
    cost_micros Int64 COMMENT 'Amount spent in micro units of the account currency, as Google reports it',
    conversions Float64 COMMENT 'Conversions counted by the account conversion actions, fractional with modelled attribution',
    conversions_value Float64 COMMENT 'Value of the conversions in the account currency'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, customer_id, currency, date, campaign_id, campaign_name, ad_group_id, ad_group_name, ad_group_status,
       toUInt64(impressions) AS impressions, toUInt64(clicks) AS clicks,
       toDecimal64(cost_micros, 6) / 1000000 AS cost, cost_micros, conversions, conversions_value
FROM raw.google_ads___performance_ad_groups;

CREATE OR REPLACE VIEW marts.google_ads_campaign_list (
    source LowCardinality(String) COMMENT 'Always google_ads',
    customer_id LowCardinality(String) COMMENT 'Google Ads customer (account) id, digits only',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the customer account',
    time_zone LowCardinality(String) COMMENT 'IANA time zone of the account. Report days are in this zone',
    campaign_id String COMMENT 'Google Ads campaign id',
    campaign_name String COMMENT 'Campaign name at the last pull',
    campaign_status LowCardinality(String) COMMENT 'ENABLED or PAUSED at the last pull. REMOVED campaigns are not pulled',
    serving_status LowCardinality(String) COMMENT 'SERVING, ENDED, PENDING, SUSPENDED or NONE',
    channel_type LowCardinality(String) COMMENT 'Advertising channel: SEARCH, DISPLAY, SHOPPING, VIDEO, PERFORMANCE_MAX, ...',
    bidding_strategy_type LowCardinality(String) COMMENT 'Bidding strategy, for example MAXIMIZE_CONVERSIONS or TARGET_ROAS',
    start_date String COMMENT 'Campaign start date YYYY-MM-DD as Google reports it',
    end_date String COMMENT 'Campaign end date YYYY-MM-DD, 2037-12-30 when open-ended',
    daily_budget Decimal(18, 6) COMMENT 'Daily budget in the account currency (budget_micros / 1 000 000)'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, customer_id, currency, time_zone, campaign_id, campaign_name, campaign_status, serving_status,
       channel_type, bidding_strategy_type, start_date, end_date, toDecimal64(budget_micros, 6) / 1000000 AS daily_budget
FROM raw.google_ads___campaigns;

CREATE OR REPLACE VIEW marts.google_ads_search_terms (
    source LowCardinality(String) COMMENT 'Always google_ads',
    report LowCardinality(String) COMMENT 'Report grain, always search_terms: one row per customer, ad group, search term and day. Undercounts the campaigns grain: Google withholds low-volume terms. Never combine grains',
    customer_id LowCardinality(String) COMMENT 'Google Ads customer (account) id, digits only',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the customer account. cost is in this currency',
    date Date COMMENT 'Day in the account time zone. The last 30 days are re-pulled daily for conversion lag and may change',
    campaign_id String COMMENT 'Google Ads campaign id',
    campaign_name String COMMENT 'Campaign name at the time of the pull',
    ad_group_id String COMMENT 'Google Ads ad group id',
    ad_group_name String COMMENT 'Ad group name at the time of the pull',
    search_term String COMMENT 'The query a user typed that triggered the ad',
    search_term_status LowCardinality(String) COMMENT 'ADDED when the term is a keyword, EXCLUDED when negative, ADDED_EXCLUDED, NONE when neither',
    impressions UInt64 COMMENT 'Times an ad was shown for the search term',
    clicks UInt64 COMMENT 'Clicks on the ads for the search term',
    cost Decimal(18, 6) COMMENT 'Amount spent in the account currency (cost_micros / 1 000 000)',
    cost_micros Int64 COMMENT 'Amount spent in micro units of the account currency, as Google reports it',
    conversions Float64 COMMENT 'Conversions counted by the account conversion actions, fractional with modelled attribution',
    conversions_value Float64 COMMENT 'Value of the conversions in the account currency'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, customer_id, currency, date, campaign_id, campaign_name, ad_group_id, ad_group_name, search_term,
       search_term_status, toUInt64(impressions) AS impressions, toUInt64(clicks) AS clicks,
       toDecimal64(cost_micros, 6) / 1000000 AS cost, cost_micros, conversions, conversions_value
FROM raw.google_ads___performance_search_terms;

CREATE OR REPLACE VIEW marts.google_ads_asset_groups (
    source LowCardinality(String) COMMENT 'Always google_ads',
    report LowCardinality(String) COMMENT 'Report grain, always asset_groups: one row per customer, Performance Max asset group and day. Sums to the campaigns grain for PERFORMANCE_MAX campaigns only. Never combine grains',
    customer_id LowCardinality(String) COMMENT 'Google Ads customer (account) id, digits only',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the customer account. cost is in this currency',
    date Date COMMENT 'Day in the account time zone. The last 30 days are re-pulled daily for conversion lag and may change',
    campaign_id String COMMENT 'Google Ads campaign id (a PERFORMANCE_MAX campaign)',
    campaign_name String COMMENT 'Campaign name at the time of the pull',
    asset_group_id String COMMENT 'Google Ads asset group id',
    asset_group_name String COMMENT 'Asset group name at the time of the pull',
    asset_group_status LowCardinality(String) COMMENT 'ENABLED, PAUSED or REMOVED at pull time',
    impressions UInt64 COMMENT 'Times an ad of the asset group was shown',
    clicks UInt64 COMMENT 'Clicks on the ads',
    cost Decimal(18, 6) COMMENT 'Amount spent in the account currency (cost_micros / 1 000 000)',
    cost_micros Int64 COMMENT 'Amount spent in micro units of the account currency, as Google reports it',
    conversions Float64 COMMENT 'Conversions counted by the account conversion actions, fractional with modelled attribution',
    conversions_value Float64 COMMENT 'Value of the conversions in the account currency'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, customer_id, currency, date, campaign_id, campaign_name, asset_group_id, asset_group_name,
       asset_group_status, toUInt64(impressions) AS impressions, toUInt64(clicks) AS clicks,
       toDecimal64(cost_micros, 6) / 1000000 AS cost, cost_micros, conversions, conversions_value
FROM raw.google_ads___performance_asset_groups;
