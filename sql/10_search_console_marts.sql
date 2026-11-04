-- Search Console marts: one view per report grain over the dlt tables in raw (DESIGN.md §4).
-- Applied by omw.pipelines.search_console after each successful load (CREATE OR REPLACE, idempotent).
-- SQL SECURITY DEFINER: the views run with the pipeline user's rights, so the agent reads them with its
-- SELECT on marts.* alone and raw stays invisible to it (DESIGN.md §1).
-- Grains sit side by side and are never summed together; a query filters on exactly one `report`.
-- Breakdown grains may undercount `core` (Google withholds anonymised queries and rare pages), never overcount.

CREATE OR REPLACE VIEW marts.search_console_core (
    source LowCardinality(String) COMMENT 'Always search_console',
    report LowCardinality(String) COMMENT 'Report grain, always core: one row per property, search type and day. The reference total for the breakdown grains',
    site_url LowCardinality(String) COMMENT 'Search Console property: sc-domain:example.com or a URL prefix such as https://example.com/',
    search_type LowCardinality(String) COMMENT 'Google search surface: web, image, video, news, discover or googleNews. Never sum across types',
    date Date COMMENT 'Day in Pacific time as Google reports it. The latest 2-3 days are provisional and re-pulled daily. Backfill reaches back 16 months',
    clicks UInt64 COMMENT 'Clicks from Google Search results to the property',
    impressions UInt64 COMMENT 'Times a result from the property appeared in search results',
    ctr Float64 COMMENT 'clicks / impressions as Google computes it, 0 to 1. A rate: never sum it, recompute from clicks and impressions',
    position Float64 COMMENT 'Average top position of the property in results, 1 = first. A rate: never sum it'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, site_url, search_type, date, toUInt64(clicks) AS clicks, toUInt64(impressions) AS impressions, ctr, position
FROM raw.search_console___core;

CREATE OR REPLACE VIEW marts.search_console_queries (
    source LowCardinality(String) COMMENT 'Always search_console',
    report LowCardinality(String) COMMENT 'Report grain, always queries: one row per property, search type, day and search query. Undercounts core: Google withholds anonymised (rare or private) queries',
    site_url LowCardinality(String) COMMENT 'Search Console property: sc-domain:example.com or a URL prefix',
    search_type LowCardinality(String) COMMENT 'Google search surface: web, image, video, news, discover or googleNews',
    date Date COMMENT 'Day in Pacific time as Google reports it. The latest 2-3 days are provisional and re-pulled daily',
    query String COMMENT 'Search query text as the user typed it, lower-cased by Google',
    clicks UInt64 COMMENT 'Clicks on the property for this query',
    impressions UInt64 COMMENT 'Impressions of the property for this query',
    ctr Float64 COMMENT 'clicks / impressions, 0 to 1. A rate: never sum it',
    position Float64 COMMENT 'Average position for this query, 1 = first. A rate: never sum it'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, site_url, search_type, date, query, toUInt64(clicks) AS clicks, toUInt64(impressions) AS impressions, ctr, position
FROM raw.search_console___queries;

CREATE OR REPLACE VIEW marts.search_console_pages (
    source LowCardinality(String) COMMENT 'Always search_console',
    report LowCardinality(String) COMMENT 'Report grain, always pages: one row per property, search type, day and landing page URL. Can undercount core',
    site_url LowCardinality(String) COMMENT 'Search Console property: sc-domain:example.com or a URL prefix',
    search_type LowCardinality(String) COMMENT 'Google search surface: web, image, video, news, discover or googleNews',
    date Date COMMENT 'Day in Pacific time as Google reports it. The latest 2-3 days are provisional and re-pulled daily',
    page String COMMENT 'Canonical URL of the page shown in results',
    clicks UInt64 COMMENT 'Clicks to this page',
    impressions UInt64 COMMENT 'Impressions of this page',
    ctr Float64 COMMENT 'clicks / impressions, 0 to 1. A rate: never sum it',
    position Float64 COMMENT 'Average position of this page, 1 = first. A rate: never sum it'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, site_url, search_type, date, page, toUInt64(clicks) AS clicks, toUInt64(impressions) AS impressions, ctr, position
FROM raw.search_console___pages;

CREATE OR REPLACE VIEW marts.search_console_query_page (
    source LowCardinality(String) COMMENT 'Always search_console',
    report LowCardinality(String) COMMENT 'Report grain, always query_page: one row per property, search type, day, query and page. Undercounts core like queries does',
    site_url LowCardinality(String) COMMENT 'Search Console property: sc-domain:example.com or a URL prefix',
    search_type LowCardinality(String) COMMENT 'Google search surface: web, image, video, news, discover or googleNews',
    date Date COMMENT 'Day in Pacific time as Google reports it. The latest 2-3 days are provisional and re-pulled daily',
    query String COMMENT 'Search query text, lower-cased by Google',
    page String COMMENT 'Canonical URL of the page shown for the query',
    clicks UInt64 COMMENT 'Clicks on this page for this query',
    impressions UInt64 COMMENT 'Impressions of this page for this query',
    ctr Float64 COMMENT 'clicks / impressions, 0 to 1. A rate: never sum it',
    position Float64 COMMENT 'Average position of this page for this query, 1 = first. A rate: never sum it'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, site_url, search_type, date, query, page, toUInt64(clicks) AS clicks, toUInt64(impressions) AS impressions, ctr, position
FROM raw.search_console___query_page;

CREATE OR REPLACE VIEW marts.search_console_countries (
    source LowCardinality(String) COMMENT 'Always search_console',
    report LowCardinality(String) COMMENT 'Report grain, always countries: one row per property, search type, day and country. Sums to core except for a small unknown remainder',
    site_url LowCardinality(String) COMMENT 'Search Console property: sc-domain:example.com or a URL prefix',
    search_type LowCardinality(String) COMMENT 'Google search surface: web, image, video, news, discover or googleNews',
    date Date COMMENT 'Day in Pacific time as Google reports it. The latest 2-3 days are provisional and re-pulled daily',
    country LowCardinality(String) COMMENT 'ISO 3166-1 alpha-3 country code in lower case as Google reports it (usa, deu, ...)',
    clicks UInt64 COMMENT 'Clicks from searches in this country',
    impressions UInt64 COMMENT 'Impressions in this country',
    ctr Float64 COMMENT 'clicks / impressions, 0 to 1. A rate: never sum it',
    position Float64 COMMENT 'Average position in this country, 1 = first. A rate: never sum it'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, site_url, search_type, date, country, toUInt64(clicks) AS clicks, toUInt64(impressions) AS impressions, ctr, position
FROM raw.search_console___countries;

CREATE OR REPLACE VIEW marts.search_console_devices (
    source LowCardinality(String) COMMENT 'Always search_console',
    report LowCardinality(String) COMMENT 'Report grain, always devices: one row per property, search type, day and device class. Sums to core',
    site_url LowCardinality(String) COMMENT 'Search Console property: sc-domain:example.com or a URL prefix',
    search_type LowCardinality(String) COMMENT 'Google search surface: web, image, video, news, discover or googleNews',
    date Date COMMENT 'Day in Pacific time as Google reports it. The latest 2-3 days are provisional and re-pulled daily',
    device LowCardinality(String) COMMENT 'Device class as Google reports it: DESKTOP, MOBILE or TABLET',
    clicks UInt64 COMMENT 'Clicks from this device class',
    impressions UInt64 COMMENT 'Impressions on this device class',
    ctr Float64 COMMENT 'clicks / impressions, 0 to 1. A rate: never sum it',
    position Float64 COMMENT 'Average position on this device class, 1 = first. A rate: never sum it'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, report, site_url, search_type, date, device, toUInt64(clicks) AS clicks, toUInt64(impressions) AS impressions, ctr, position
FROM raw.search_console___devices;
