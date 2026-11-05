-- Meta lead ads (DESIGN.md §4): one row per lead submitted on an ad, without the answers (personal data stays in raw).
-- Applied by omw.pipelines.meta_ads after each load with --leads. SQL SECURITY DEFINER keeps raw invisible to the agent.
CREATE OR REPLACE VIEW marts.meta_ads_leads (
    source LowCardinality(String) COMMENT 'Always meta_ads',
    report LowCardinality(String) COMMENT 'Report grain, always leads: one row per lead-ad submission. Count rows for leads',
    account_id LowCardinality(String) COMMENT 'Meta ad account id without the act_ prefix',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the ad account (from its insights), empty until insights were pulled',
    date Date COMMENT 'Day the lead was submitted (UTC)',
    campaign_id String COMMENT 'Meta campaign id',
    campaign_name String COMMENT 'Campaign name as Meta reports it on the lead',
    adset_id String COMMENT 'Meta ad set id',
    adset_name String COMMENT 'Ad set name as Meta reports it on the lead',
    ad_id String COMMENT 'Meta ad id',
    ad_name String COMMENT 'Ad name as Meta reports it on the lead',
    form_id String COMMENT 'Lead form id',
    is_organic Bool COMMENT 'True when the lead came from the organic (unpaid) form, false when from the ad',
    platform LowCardinality(String) COMMENT 'fb or ig',
    lead_id String COMMENT 'Meta lead id',
    submitted_at DateTime COMMENT 'When the lead was submitted (UTC)',
    answer_count UInt64 COMMENT 'Number of answered form fields. The answers themselves are not exposed here'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT l.source AS source, 'leads' AS report, l.account_id AS account_id, a.currency AS currency, l.date AS date,
       l.campaign_id AS campaign_id, l.campaign_name AS campaign_name, l.adset_id AS adset_id, l.adset_name AS adset_name,
       l.ad_id AS ad_id, l.ad_name AS ad_name, l.form_id AS form_id, l.is_organic AS is_organic, l.platform AS platform,
       l.id AS lead_id, l.created_time AS submitted_at, toUInt64(l.field_count) AS answer_count
FROM raw.meta_ads___leads AS l
LEFT JOIN (SELECT account_id, any(currency) AS currency FROM raw.meta_ads___insights_campaigns GROUP BY account_id) AS a
    ON a.account_id = l.account_id;
