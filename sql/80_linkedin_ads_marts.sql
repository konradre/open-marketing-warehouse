-- LinkedIn Ads marts (DESIGN.md §4): campaign and creative daily analytics and the campaign list with budgets.
-- Applied by omw.pipelines.linkedin_ads after each successful load. SQL SECURITY DEFINER keeps raw invisible to the agent.

CREATE OR REPLACE VIEW marts.linkedin_ads_campaigns (
    source LowCardinality(String) COMMENT 'Always linkedin_ads',
    report LowCardinality(String) COMMENT 'Report grain, always campaigns: one row per sponsored account, campaign and day',
    account_id LowCardinality(String) COMMENT 'LinkedIn sponsored account id (digits of urn:li:sponsoredAccount)',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the sponsored account. cost is in this currency',
    date Date COMMENT 'Day in UTC as LinkedIn reports daily analytics. Cost and conversions settle over a few days: the last 7 days are re-pulled daily',
    campaign_id String COMMENT 'LinkedIn campaign id (digits of urn:li:sponsoredCampaign)',
    campaign_name String COMMENT 'Campaign name at the last entity pull, empty when the campaign is no longer listed',
    impressions UInt64 COMMENT 'Times an ad of the campaign was shown',
    clicks UInt64 COMMENT 'Chargeable clicks on the ads',
    cost Decimal(18, 2) COMMENT 'Amount spent in the account currency (costInLocalCurrency)',
    conversions UInt64 COMMENT 'External website conversions attributed by the LinkedIn Insight Tag (post-click and post-view)',
    landing_page_clicks UInt64 COMMENT 'Clicks that opened the landing page',
    likes UInt64 COMMENT 'Reactions on the sponsored content',
    shares UInt64 COMMENT 'Shares of the sponsored content',
    follows UInt64 COMMENT 'Follows gained from the ads'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT a.source AS source, a.report AS report, a.account_id AS account_id, a.currency AS currency, a.date AS date,
       a.campaign_id AS campaign_id, c.name AS campaign_name, toUInt64(a.impressions) AS impressions,
       toUInt64(a.clicks) AS clicks, toDecimal64(a.cost, 2) AS cost, toUInt64(a.conversions) AS conversions,
       toUInt64(a.landing_page_clicks) AS landing_page_clicks, toUInt64(a.likes) AS likes, toUInt64(a.shares) AS shares,
       toUInt64(a.follows) AS follows
FROM raw.linkedin_ads___ad_analytics AS a
LEFT JOIN raw.linkedin_ads___campaigns AS c ON c.id = a.campaign_id;

CREATE OR REPLACE VIEW marts.linkedin_ads_campaign_list (
    source LowCardinality(String) COMMENT 'Always linkedin_ads',
    account_id LowCardinality(String) COMMENT 'LinkedIn sponsored account id',
    campaign_group_id String COMMENT 'Campaign group the campaign belongs to',
    campaign_group_name String COMMENT 'Campaign group name at the last pull',
    group_daily_budget Decimal(18, 2) COMMENT 'Daily budget set on the campaign group (campaign group budget optimisation), 0 when budgets are per campaign',
    group_total_budget Decimal(18, 2) COMMENT 'Lifetime budget set on the campaign group, 0 when none',
    group_budget_optimization Bool COMMENT 'True when LinkedIn distributes the group budget across its campaigns automatically',
    campaign_id String COMMENT 'LinkedIn campaign id',
    campaign_name String COMMENT 'Campaign name at the last pull',
    status LowCardinality(String) COMMENT 'ACTIVE, PAUSED, ARCHIVED, COMPLETED, CANCELED, DRAFT or PENDING_DELETION',
    type LowCardinality(String) COMMENT 'SPONSORED_UPDATES, TEXT_AD, SPONSORED_INMAILS, DYNAMIC',
    objective_type LowCardinality(String) COMMENT 'BRAND_AWARENESS, WEBSITE_VISIT, LEAD_GENERATION, WEBSITE_CONVERSION, ...',
    cost_type LowCardinality(String) COMMENT 'CPM, CPC or CPV',
    optimization_target_type LowCardinality(String) COMMENT 'Bidding optimisation target, for example MAX_CLICK or MAX_CONVERSION',
    daily_budget Decimal(18, 2) COMMENT 'Daily budget of the campaign in the account currency, 0 when the group budget applies',
    unit_cost Decimal(18, 2) COMMENT 'Bid per unit (click, 1000 impressions or view) in the account currency',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the budgets and bids',
    run_start DateTime COMMENT 'Scheduled start (UTC)',
    run_end DateTime COMMENT 'Scheduled end (UTC), 1970-01-01 when open-ended'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT c.source AS source, c.account_id AS account_id, c.campaign_group_id AS campaign_group_id, g.name AS campaign_group_name,
       toDecimal64(g.daily_budget, 2) AS group_daily_budget, toDecimal64(g.total_budget, 2) AS group_total_budget,
       g.budget_optimization AS group_budget_optimization, c.id AS campaign_id, c.name AS campaign_name, c.status AS status,
       c.type AS type, c.objective_type AS objective_type, c.cost_type AS cost_type,
       c.optimization_target_type AS optimization_target_type, toDecimal64(c.daily_budget, 2) AS daily_budget,
       toDecimal64(c.unit_cost, 2) AS unit_cost, c.currency AS currency, c.run_start AS run_start, c.run_end AS run_end
FROM raw.linkedin_ads___campaigns AS c
LEFT JOIN raw.linkedin_ads___campaign_groups AS g ON g.id = c.campaign_group_id;

CREATE OR REPLACE VIEW marts.linkedin_ads_creatives (
    source LowCardinality(String) COMMENT 'Always linkedin_ads',
    report LowCardinality(String) COMMENT 'Report grain, always creatives: one row per sponsored account, creative and day. Sums to the campaigns grain. Never combine grains',
    account_id LowCardinality(String) COMMENT 'LinkedIn sponsored account id',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the sponsored account. cost is in this currency',
    date Date COMMENT 'Day in UTC as LinkedIn reports daily analytics. The last 7 days are re-pulled daily',
    campaign_id String COMMENT 'LinkedIn campaign the creative belongs to (from the creatives entity list), empty when the creative is no longer listed',
    campaign_name String COMMENT 'Campaign name at the last entity pull, empty when unknown',
    creative_id String COMMENT 'LinkedIn creative id (digits of urn:li:sponsoredCreative)',
    creative_status LowCardinality(String) COMMENT 'Intended status of the creative at the last entity pull: ACTIVE, PAUSED, ARCHIVED, ...',
    is_serving Bool COMMENT 'Whether LinkedIn reported the creative as serving at the last entity pull',
    impressions UInt64 COMMENT 'Times the creative was shown',
    clicks UInt64 COMMENT 'Chargeable clicks on the creative',
    cost Decimal(18, 2) COMMENT 'Amount spent in the account currency (costInLocalCurrency)',
    conversions UInt64 COMMENT 'External website conversions attributed by the LinkedIn Insight Tag (post-click and post-view)',
    landing_page_clicks UInt64 COMMENT 'Clicks that opened the landing page',
    likes UInt64 COMMENT 'Reactions on the creative',
    shares UInt64 COMMENT 'Shares of the creative',
    follows UInt64 COMMENT 'Follows gained from the creative'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT a.source AS source, a.report AS report, a.account_id AS account_id, a.currency AS currency, a.date AS date,
       cr.campaign_id AS campaign_id, c.name AS campaign_name, a.creative_id AS creative_id,
       cr.intended_status AS creative_status, cr.is_serving AS is_serving, toUInt64(a.impressions) AS impressions,
       toUInt64(a.clicks) AS clicks, toDecimal64(a.cost, 2) AS cost, toUInt64(a.conversions) AS conversions,
       toUInt64(a.landing_page_clicks) AS landing_page_clicks, toUInt64(a.likes) AS likes, toUInt64(a.shares) AS shares,
       toUInt64(a.follows) AS follows
FROM raw.linkedin_ads___ad_analytics_creatives AS a
LEFT JOIN raw.linkedin_ads___creatives AS cr ON cr.id = a.creative_id
LEFT JOIN raw.linkedin_ads___campaigns AS c ON c.id = cr.campaign_id;
