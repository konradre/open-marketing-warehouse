-- Twenty CRM change feed (DESIGN.md §4): one row per timeline activity, Twenty's own record of what happened to a
-- company, person, opportunity, note or task and when. It dates what the pipeline view cannot: twenty_pipeline shows
-- each opportunity at its stage today, this view shows the day it moved.
-- The closed-won stages are configuration (sources.twenty.won_stages, CUSTOMER by default): the pipeline rewrites the
-- lists between the /*won_stages*/ markers and the stage names at the end of the became_won comment before it applies the file.
-- Applied by omw.pipelines.twenty after a load, once the timeline table exists. SQL SECURITY DEFINER keeps raw
-- invisible to the agent; the before and after values of an update stay in raw.

CREATE OR REPLACE VIEW marts.twenty_timeline (
    source LowCardinality(String) COMMENT 'Always twenty',
    report LowCardinality(String) COMMENT 'Report grain, always timeline: one row per event Twenty recorded on a record (soft-deleted events excluded). Twenty may fold quick successive edits of one record by one member into a single event',
    date Date COMMENT 'Day the event happened (UTC)',
    event_id String COMMENT 'Twenty timeline activity id',
    happens_at DateTime COMMENT 'When the event happened (UTC)',
    event LowCardinality(String) COMMENT 'Event name as Twenty records it, for example opportunity.updated or linked-note.created',
    object LowCardinality(String) COMMENT 'Kind of record the event is about: opportunity, person, company, note, task. For a linked event, the linked record (the note or task)',
    action LowCardinality(String) COMMENT 'created, updated, deleted, restored, linked or unlinked',
    is_linked Bool COMMENT 'True when the event is about a note or task linked to the record, not about the record itself',
    changed_fields String COMMENT 'Names of the fields an update changed, comma separated and sorted, for example amount,stage. Empty for other events',
    stage_before LowCardinality(String) COMMENT 'Stage an opportunity left when the event changed its stage, empty otherwise',
    stage_after LowCardinality(String) COMMENT 'Stage an opportunity entered when the event changed its stage, empty otherwise',
    became_won Bool COMMENT 'True when the event moved an opportunity into a closed-won stage from a stage that is not one. Closed-won stages here: CUSTOMER',
    member_id String COMMENT 'Workspace member who caused the event, empty for the system or an API key',
    person_id String COMMENT 'Person the event happened on, empty when none',
    company_id String COMMENT 'Company the event happened on, else the company of the opportunity it happened on, empty when none',
    company_name String COMMENT 'Company name at the last sync',
    opportunity_id String COMMENT 'Opportunity the event happened on, empty when none',
    opportunity_name String COMMENT 'Opportunity name at the last sync',
    campaign_key String COMMENT 'Campaign key of the opportunity, else person, else company the event happened on (DESIGN.md §6), empty when unattributed'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT t.source AS source, 'timeline' AS report, toDate(t.happens_at) AS date, t.id AS event_id, t.happens_at AS happens_at,
       t.event AS event, t.object_name AS object, t.action AS action, t.is_linked AS is_linked, t.changed_fields AS changed_fields,
       t.stage_before AS stage_before, t.stage_after AS stage_after,
       (t.stage_after IN (/*won_stages*/'CUSTOMER'/*won_stages*/) AND t.stage_before NOT IN (/*won_stages*/'CUSTOMER'/*won_stages*/)) AS became_won,
       t.member_id AS member_id, t.person_id AS person_id,
       if(t.company_id != '', t.company_id, o.company_id) AS company_id, if(c.name != '', c.name, oc.name) AS company_name,
       t.opportunity_id AS opportunity_id, o.name AS opportunity_name,
       multiIf(o.campaign_key != '', o.campaign_key, p.campaign_key != '', p.campaign_key, c.campaign_key != '', c.campaign_key,
               oc.campaign_key) AS campaign_key
FROM raw.twenty___timeline_activities AS t
LEFT JOIN raw.twenty___opportunities AS o ON o.id = t.opportunity_id
LEFT JOIN raw.twenty___people AS p ON p.id = t.person_id
LEFT JOIN raw.twenty___companies AS c ON c.id = t.company_id
LEFT JOIN raw.twenty___companies AS oc ON oc.id = o.company_id
WHERE t.deleted_at = toDateTime('1970-01-01 00:00:00');
