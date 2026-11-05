-- Twenty CRM marts (DESIGN.md §4): the sales pipeline by opportunity, by stage and campaign key, and the sales activity
-- (notes and tasks) attached to companies, people and opportunities.
-- Attribution custom fields resolve opportunity, then its point of contact, then its company.
-- Applied by omw.pipelines.twenty after each successful load. SQL SECURITY DEFINER keeps raw invisible to the agent.

CREATE OR REPLACE VIEW marts.twenty_pipeline (
    source LowCardinality(String) COMMENT 'Always twenty',
    report LowCardinality(String) COMMENT 'Report grain, always opportunities: one row per open or closed opportunity (soft-deleted ones excluded)',
    date Date COMMENT 'Day the opportunity was created in Twenty (UTC). The metric layer dates pipeline by creation, so an opportunity counts on the day it entered the pipeline whatever its stage today',
    opportunity_id String COMMENT 'Twenty opportunity id',
    opportunity_name String COMMENT 'Opportunity name at the last sync',
    stage LowCardinality(String) COMMENT 'Pipeline stage as configured in Twenty, by default NEW, SCREENING, MEETING, PROPOSAL, CUSTOMER',
    amount Decimal(18, 2) COMMENT 'Opportunity amount in its currency (amountMicros / 1 000 000)',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the amount, empty when unset',
    close_date DateTime COMMENT 'Expected or actual close date (UTC), 1970-01-01 when unset',
    created_at DateTime COMMENT 'When the opportunity was created in Twenty (UTC)',
    updated_at DateTime COMMENT 'When it was last changed in Twenty (UTC)',
    company_id String COMMENT 'Twenty company id, empty when none',
    company_name String COMMENT 'Company name at the last sync',
    company_domain String COMMENT 'Company website domain',
    person_id String COMMENT 'Point of contact (Twenty person id), empty when none',
    person_email String COMMENT 'Point of contact email',
    campaign_key String COMMENT 'Campaign key for cross-source joins (DESIGN.md §6): the campaignKey custom field on the opportunity, else the person, else the company. Empty when absent',
    utm_source String COMMENT 'utmSource custom field, same precedence',
    utm_medium String COMMENT 'utmMedium custom field, same precedence',
    utm_campaign String COMMENT 'utmCampaign custom field, same precedence',
    attributed Bool COMMENT 'True when any attribution field is set. Unattributed pipeline is real pipeline: never drop it when comparing to spend'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT o.source AS source, 'opportunities' AS report, toDate(o.created_at) AS date, o.id AS opportunity_id, o.name AS opportunity_name, o.stage AS stage,
       toDecimal64(o.amount, 2) AS amount, o.currency AS currency, o.close_date AS close_date, o.created_at AS created_at,
       o.updated_at AS updated_at, o.company_id AS company_id, c.name AS company_name, c.domain AS company_domain,
       o.person_id AS person_id, p.email AS person_email,
       multiIf(o.campaign_key != '', o.campaign_key, p.campaign_key != '', p.campaign_key, c.campaign_key) AS campaign_key,
       multiIf(o.utm_source != '', o.utm_source, p.utm_source != '', p.utm_source, c.utm_source) AS utm_source,
       multiIf(o.utm_medium != '', o.utm_medium, p.utm_medium != '', p.utm_medium, c.utm_medium) AS utm_medium,
       multiIf(o.utm_campaign != '', o.utm_campaign, p.utm_campaign != '', p.utm_campaign, c.utm_campaign) AS utm_campaign,
       (campaign_key != '' OR utm_source != '' OR utm_medium != '' OR utm_campaign != '') AS attributed
FROM raw.twenty___opportunities AS o
LEFT JOIN raw.twenty___people AS p ON p.id = o.person_id
LEFT JOIN raw.twenty___companies AS c ON c.id = o.company_id
WHERE o.deleted_at = toDateTime('1970-01-01 00:00:00');

CREATE OR REPLACE VIEW marts.twenty_pipeline_by_stage (
    source LowCardinality(String) COMMENT 'Always twenty',
    report LowCardinality(String) COMMENT 'Report grain, always pipeline_by_stage: one row per stage, currency and campaign key. Sums to twenty_pipeline',
    stage LowCardinality(String) COMMENT 'Pipeline stage as configured in Twenty',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency, empty when unset. Never sum across currencies',
    campaign_key String COMMENT 'Campaign key for cross-source joins, empty for unattributed pipeline',
    utm_source String COMMENT 'utm_source of the attribution, empty when absent',
    utm_medium String COMMENT 'utm_medium of the attribution, empty when absent',
    utm_campaign String COMMENT 'utm_campaign of the attribution, empty when absent',
    opportunities UInt64 COMMENT 'Opportunities in the stage',
    amount Decimal(18, 2) COMMENT 'Sum of opportunity amounts in the currency'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, 'pipeline_by_stage' AS report, stage, currency, campaign_key, utm_source, utm_medium, utm_campaign,
       count() AS opportunities, sum(amount) AS amount
FROM marts.twenty_pipeline
GROUP BY source, stage, currency, campaign_key, utm_source, utm_medium, utm_campaign;

CREATE OR REPLACE VIEW marts.twenty_activity (
    source LowCardinality(String) COMMENT 'Always twenty',
    report LowCardinality(String) COMMENT 'Report grain, always activity: one row per note or task and the record it is attached to (a note on two records is two rows). Count distinct activity_id for activities',
    date Date COMMENT 'Day the note or task was created (UTC)',
    activity_id String COMMENT 'Twenty note or task id',
    kind LowCardinality(String) COMMENT 'note or task',
    title String COMMENT 'Title of the note or task',
    status LowCardinality(String) COMMENT 'Task status (TODO, IN_PROGRESS, DONE as configured), empty for notes',
    due_at DateTime COMMENT 'Task due date (UTC), 1970-01-01 when unset or for notes',
    created_by String COMMENT 'Workspace member or system that created it',
    person_id String COMMENT 'Person the activity is attached to, empty when none',
    company_id String COMMENT 'Company the activity is attached to, else the company of the attached opportunity, empty when none',
    company_name String COMMENT 'Company name at the last sync',
    opportunity_id String COMMENT 'Opportunity the activity is attached to, empty when none',
    opportunity_name String COMMENT 'Opportunity name at the last sync',
    campaign_key String COMMENT 'Campaign key of the attached opportunity, else person, else company (DESIGN.md §6), empty when unattributed',
    created_at DateTime COMMENT 'When the note or task was created (UTC)'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT a.source AS source, 'activity' AS report, toDate(a.created_at) AS date, a.id AS activity_id, a.kind AS kind,
       a.title AS title, a.status AS status, a.due_at AS due_at, a.created_by AS created_by, t.person_id AS person_id,
       if(t.company_id != '', t.company_id, o.company_id) AS company_id, if(c.name != '', c.name, oc.name) AS company_name,
       t.opportunity_id AS opportunity_id, o.name AS opportunity_name,
       multiIf(o.campaign_key != '', o.campaign_key, p.campaign_key != '', p.campaign_key, c.campaign_key != '', c.campaign_key,
               oc.campaign_key) AS campaign_key,
       a.created_at AS created_at
FROM (
    SELECT source, id, 'note' AS kind, title, '' AS status, toDateTime(0) AS due_at, created_by, created_at, deleted_at
    FROM raw.twenty___notes
    UNION ALL
    SELECT source, id, 'task' AS kind, title, status, due_at, created_by, created_at, deleted_at
    FROM raw.twenty___tasks
) AS a
LEFT JOIN (
    SELECT note_id AS activity_id, person_id, company_id, opportunity_id FROM raw.twenty___note_targets
    WHERE deleted_at = toDateTime('1970-01-01 00:00:00')
    UNION ALL
    SELECT task_id AS activity_id, person_id, company_id, opportunity_id FROM raw.twenty___task_targets
    WHERE deleted_at = toDateTime('1970-01-01 00:00:00')
) AS t ON t.activity_id = a.id
LEFT JOIN raw.twenty___opportunities AS o ON o.id = t.opportunity_id
LEFT JOIN raw.twenty___people AS p ON p.id = t.person_id
LEFT JOIN raw.twenty___companies AS c ON c.id = t.company_id
LEFT JOIN raw.twenty___companies AS oc ON oc.id = o.company_id
WHERE a.deleted_at = toDateTime('1970-01-01 00:00:00');
