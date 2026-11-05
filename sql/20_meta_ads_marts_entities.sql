-- Meta Ads entity lists (DESIGN.md §4): campaigns and ad sets with their budgets, as last pulled.
-- Budgets are Meta's strings in the account currency's minor units (whole units for zero-decimal currencies);
-- the currency comes from the insights of the same account. Applied by omw.pipelines.meta_ads after each load
-- that fetched entities. SQL SECURITY DEFINER keeps raw invisible to the agent.

CREATE OR REPLACE VIEW marts.meta_ads_campaign_list (
    source LowCardinality(String) COMMENT 'Always meta_ads',
    account_id LowCardinality(String) COMMENT 'Meta ad account id without the act_ prefix',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the ad account (from its insights), empty until insights were pulled',
    campaign_id String COMMENT 'Meta campaign id',
    campaign_name String COMMENT 'Campaign name at the last pull',
    status LowCardinality(String) COMMENT 'Configured status: ACTIVE, PAUSED, DELETED or ARCHIVED',
    effective_status LowCardinality(String) COMMENT 'Status after account and schedule rules, for example ACTIVE, PAUSED, CAMPAIGN_PAUSED, IN_PROCESS',
    objective LowCardinality(String) COMMENT 'Campaign objective, for example OUTCOME_SALES or OUTCOME_LEADS',
    buying_type LowCardinality(String) COMMENT 'AUCTION or RESERVED',
    daily_budget Decimal(18, 2) COMMENT 'Daily budget in the account currency, 0 when the budget is lifetime or set on the ad sets',
    lifetime_budget Decimal(18, 2) COMMENT 'Lifetime budget in the account currency, 0 when none',
    start_time DateTime COMMENT 'Scheduled start (UTC), 1970-01-01 when unset',
    stop_time DateTime COMMENT 'Scheduled end (UTC), 1970-01-01 when open-ended',
    created_time DateTime COMMENT 'When the campaign was created (UTC)',
    updated_time DateTime COMMENT 'When it was last changed in Meta (UTC)'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT 'meta_ads' AS source, c.account_id AS account_id, a.currency AS currency, c.id AS campaign_id, c.name AS campaign_name,
       c.status AS status, c.effective_status AS effective_status, c.objective AS objective, c.buying_type AS buying_type,
       if(a.currency IN ('CLP','ISK','JPY','KRW','PYG','TWD','VND','HUF','COP'), toDecimal64(toInt64OrZero(c.daily_budget), 2),
          toDecimal64(toInt64OrZero(c.daily_budget), 2) / 100) AS daily_budget,
       if(a.currency IN ('CLP','ISK','JPY','KRW','PYG','TWD','VND','HUF','COP'), toDecimal64(toInt64OrZero(c.lifetime_budget), 2),
          toDecimal64(toInt64OrZero(c.lifetime_budget), 2) / 100) AS lifetime_budget,
       ifNull(c.start_time, toDateTime(0)) AS start_time, ifNull(c.stop_time, toDateTime(0)) AS stop_time,
       c.created_time AS created_time, c.updated_time AS updated_time
FROM raw.meta_ads___campaigns AS c
LEFT JOIN (SELECT account_id, any(currency) AS currency FROM raw.meta_ads___insights_campaigns GROUP BY account_id) AS a
    ON a.account_id = c.account_id;

CREATE OR REPLACE VIEW marts.meta_ads_ad_set_list (
    source LowCardinality(String) COMMENT 'Always meta_ads',
    account_id LowCardinality(String) COMMENT 'Meta ad account id without the act_ prefix',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the ad account (from its insights), empty until insights were pulled',
    campaign_id String COMMENT 'Meta campaign the ad set belongs to',
    campaign_name String COMMENT 'Campaign name at the last pull',
    adset_id String COMMENT 'Meta ad set id',
    adset_name String COMMENT 'Ad set name at the last pull',
    status LowCardinality(String) COMMENT 'Configured status: ACTIVE, PAUSED, DELETED or ARCHIVED',
    effective_status LowCardinality(String) COMMENT 'Status after campaign and schedule rules',
    optimization_goal LowCardinality(String) COMMENT 'What delivery optimises for, for example OFFSITE_CONVERSIONS or LINK_CLICKS',
    billing_event LowCardinality(String) COMMENT 'What is charged: IMPRESSIONS, LINK_CLICKS, ...',
    bid_strategy LowCardinality(String) COMMENT 'LOWEST_COST_WITHOUT_CAP, LOWEST_COST_WITH_BID_CAP, COST_CAP, ...',
    bid_amount Decimal(18, 2) COMMENT 'Bid cap or cost cap in the account currency, 0 when none',
    daily_budget Decimal(18, 2) COMMENT 'Daily budget in the account currency, 0 when the campaign budget applies',
    lifetime_budget Decimal(18, 2) COMMENT 'Lifetime budget in the account currency, 0 when none',
    start_time DateTime COMMENT 'Scheduled start (UTC), 1970-01-01 when unset',
    end_time DateTime COMMENT 'Scheduled end (UTC), 1970-01-01 when open-ended',
    created_time DateTime COMMENT 'When the ad set was created (UTC)',
    updated_time DateTime COMMENT 'When it was last changed in Meta (UTC)'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT 'meta_ads' AS source, s.account_id AS account_id, a.currency AS currency, s.campaign_id AS campaign_id,
       c.name AS campaign_name, s.id AS adset_id, s.name AS adset_name, s.status AS status,
       s.effective_status AS effective_status, s.optimization_goal AS optimization_goal, s.billing_event AS billing_event,
       s.bid_strategy AS bid_strategy,
       if(a.currency IN ('CLP','ISK','JPY','KRW','PYG','TWD','VND','HUF','COP'), toDecimal64(toInt64OrZero(s.bid_amount), 2),
          toDecimal64(toInt64OrZero(s.bid_amount), 2) / 100) AS bid_amount,
       if(a.currency IN ('CLP','ISK','JPY','KRW','PYG','TWD','VND','HUF','COP'), toDecimal64(toInt64OrZero(s.daily_budget), 2),
          toDecimal64(toInt64OrZero(s.daily_budget), 2) / 100) AS daily_budget,
       if(a.currency IN ('CLP','ISK','JPY','KRW','PYG','TWD','VND','HUF','COP'), toDecimal64(toInt64OrZero(s.lifetime_budget), 2),
          toDecimal64(toInt64OrZero(s.lifetime_budget), 2) / 100) AS lifetime_budget,
       ifNull(s.start_time, toDateTime(0)) AS start_time, ifNull(s.end_time, toDateTime(0)) AS end_time,
       s.created_time AS created_time, s.updated_time AS updated_time
FROM raw.meta_ads___ad_sets AS s
LEFT JOIN raw.meta_ads___campaigns AS c ON c.id = s.campaign_id
LEFT JOIN (SELECT account_id, any(currency) AS currency FROM raw.meta_ads___insights_campaigns GROUP BY account_id) AS a
    ON a.account_id = s.account_id;
