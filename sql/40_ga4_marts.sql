-- GA4 marts (DESIGN.md §4): one view per report grain. The (other) row GA4 folds high-cardinality values into
-- is kept and flagged with is_other so breakdowns still sum to core. Applied by omw.pipelines.ga4 after each load.

CREATE OR REPLACE VIEW marts.ga4_core (
    source LowCardinality(String) COMMENT 'Always ga4',
    report LowCardinality(String) COMMENT 'Report grain, always core: one row per property and day. The reference total for the breakdown grains',
    property_id LowCardinality(String) COMMENT 'Numeric GA4 property id',
    date Date COMMENT 'Day in the property reporting time zone. GA4 finalises data within about 48 hours. The last 7 days are re-pulled daily',
    sessions UInt64 COMMENT 'Sessions started on the day',
    total_users UInt64 COMMENT 'Distinct users active on the day. Not additive across days: a user counts once per row',
    new_users UInt64 COMMENT 'Users whose first session was on the day',
    engaged_sessions UInt64 COMMENT 'Sessions over 10 seconds, with a key event, or with 2+ page views',
    page_views UInt64 COMMENT 'Page and screen views (screenPageViews)',
    key_events Float64 COMMENT 'Key events (formerly conversions) as configured on the property. Depends on the property setup',
    total_revenue Float64 COMMENT 'Purchase, subscription and ad revenue in the property currency, as GA4 models it. Not the billing truth: use the Stripe marts for that'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, property_id, date, toUInt64(sessions) AS sessions, toUInt64(total_users) AS total_users,
       toUInt64(new_users) AS new_users, toUInt64(engaged_sessions) AS engaged_sessions, toUInt64(page_views) AS page_views,
       key_events, total_revenue
FROM raw.ga4___core;

CREATE OR REPLACE VIEW marts.ga4_channels (
    source LowCardinality(String) COMMENT 'Always ga4',
    report LowCardinality(String) COMMENT 'Report grain, always channels: one row per property, day and session default channel group. Sums to core including the (other) row',
    property_id LowCardinality(String) COMMENT 'Numeric GA4 property id',
    date Date COMMENT 'Day in the property reporting time zone. The last 7 days are re-pulled daily',
    channel_group LowCardinality(String) COMMENT 'GA4 session default channel group: Organic Search, Paid Search, Direct, Referral, Organic Social, Email, ... or (other)',
    is_other Bool COMMENT 'True when GA4 folded this row into its (other) bucket because of cardinality limits. Disclose or exclude it',
    sessions UInt64 COMMENT 'Sessions started on the day in this channel',
    total_users UInt64 COMMENT 'Distinct users. Not additive across days or channels',
    new_users UInt64 COMMENT 'Users whose first session was on the day',
    engaged_sessions UInt64 COMMENT 'Engaged sessions',
    page_views UInt64 COMMENT 'Page and screen views',
    key_events Float64 COMMENT 'Key events (formerly conversions)',
    total_revenue Float64 COMMENT 'Modelled revenue in the property currency'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, property_id, date, channel_group, is_other, toUInt64(sessions) AS sessions, toUInt64(total_users) AS total_users,
       toUInt64(new_users) AS new_users, toUInt64(engaged_sessions) AS engaged_sessions, toUInt64(page_views) AS page_views,
       key_events, total_revenue
FROM raw.ga4___channels;

CREATE OR REPLACE VIEW marts.ga4_landing_pages (
    source LowCardinality(String) COMMENT 'Always ga4',
    report LowCardinality(String) COMMENT 'Report grain, always landing_pages: one row per property, day and landing page path. Sums to core including the (other) row',
    property_id LowCardinality(String) COMMENT 'Numeric GA4 property id',
    date Date COMMENT 'Day in the property reporting time zone. The last 7 days are re-pulled daily',
    landing_page String COMMENT 'Path of the first page of the session, without query string, or (other) / (not set)',
    is_other Bool COMMENT 'True when GA4 folded this row into its (other) bucket because of cardinality limits',
    sessions UInt64 COMMENT 'Sessions that started on this page',
    total_users UInt64 COMMENT 'Distinct users. Not additive across days or pages',
    new_users UInt64 COMMENT 'Users whose first session was on the day',
    engaged_sessions UInt64 COMMENT 'Engaged sessions',
    page_views UInt64 COMMENT 'Page and screen views in those sessions',
    key_events Float64 COMMENT 'Key events (formerly conversions)',
    total_revenue Float64 COMMENT 'Modelled revenue in the property currency'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, property_id, date, landing_page, is_other, toUInt64(sessions) AS sessions, toUInt64(total_users) AS total_users,
       toUInt64(new_users) AS new_users, toUInt64(engaged_sessions) AS engaged_sessions, toUInt64(page_views) AS page_views,
       key_events, total_revenue
FROM raw.ga4___landing_pages;

CREATE OR REPLACE VIEW marts.ga4_campaigns (
    source LowCardinality(String) COMMENT 'Always ga4',
    report LowCardinality(String) COMMENT 'Report grain, always campaigns: one row per property, day, session campaign, source and medium. Sums to core including the (other) row',
    property_id LowCardinality(String) COMMENT 'Numeric GA4 property id',
    date Date COMMENT 'Day in the property reporting time zone. The last 7 days are re-pulled daily',
    campaign String COMMENT 'Session campaign name (utm_campaign or the ad platform campaign for auto-tagged traffic), (not set) when absent, or (other)',
    source_name LowCardinality(String) COMMENT 'Session source: google, facebook, (direct), a referrer domain, ...',
    medium LowCardinality(String) COMMENT 'Session medium: organic, cpc, paid_social, referral, email, (none), ...',
    is_other Bool COMMENT 'True when GA4 folded this row into its (other) bucket because of cardinality limits',
    sessions UInt64 COMMENT 'Sessions attributed to the campaign, source and medium',
    total_users UInt64 COMMENT 'Distinct users. Not additive across days or campaigns',
    new_users UInt64 COMMENT 'Users whose first session was on the day',
    engaged_sessions UInt64 COMMENT 'Engaged sessions',
    page_views UInt64 COMMENT 'Page and screen views',
    key_events Float64 COMMENT 'Key events (formerly conversions)',
    total_revenue Float64 COMMENT 'Modelled revenue in the property currency'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, property_id, date, campaign, source_name, medium, is_other, toUInt64(sessions) AS sessions,
       toUInt64(total_users) AS total_users, toUInt64(new_users) AS new_users, toUInt64(engaged_sessions) AS engaged_sessions,
       toUInt64(page_views) AS page_views, key_events, total_revenue
FROM raw.ga4___campaigns;
