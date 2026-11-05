-- LinkedIn lead-gen form responses (DESIGN.md §4): one row per lead, without the answers (personal data stays in raw).
-- Applied by omw.pipelines.linkedin_ads after each load with --leads. SQL SECURITY DEFINER keeps raw invisible to the agent.
CREATE OR REPLACE VIEW marts.linkedin_ads_leads (
    source LowCardinality(String) COMMENT 'Always linkedin_ads',
    report LowCardinality(String) COMMENT 'Report grain, always leads: one row per lead-gen form response. Count rows for leads',
    account_id LowCardinality(String) COMMENT 'LinkedIn sponsored account id',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the sponsored account',
    date Date COMMENT 'Day the form was submitted (UTC)',
    campaign_id String COMMENT 'LinkedIn campaign the lead came from, empty when not reported',
    campaign_name String COMMENT 'Campaign name at the last entity pull, empty when unknown',
    creative_id String COMMENT 'LinkedIn creative the lead came from, empty when not reported',
    form_id String COMMENT 'Lead gen form id',
    lead_type LowCardinality(String) COMMENT 'SPONSORED (from an ad) or ORGANIC',
    lead_id String COMMENT 'LinkedIn lead form response id',
    submitted_at DateTime COMMENT 'When the form was submitted (UTC)',
    answer_count UInt64 COMMENT 'Number of answered questions. The answers themselves are not exposed here'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT l.source AS source, 'leads' AS report, l.account_id AS account_id, a.currency AS currency, l.date AS date,
       l.campaign_id AS campaign_id, c.name AS campaign_name, l.creative_id AS creative_id, l.form_id AS form_id,
       l.lead_type AS lead_type, l.id AS lead_id, l.submitted_at AS submitted_at, toUInt64(l.answer_count) AS answer_count
FROM raw.linkedin_ads___lead_form_responses AS l
LEFT JOIN raw.linkedin_ads___ad_accounts AS a ON a.id = l.account_id
LEFT JOIN raw.linkedin_ads___campaigns AS c ON c.id = l.campaign_id;
