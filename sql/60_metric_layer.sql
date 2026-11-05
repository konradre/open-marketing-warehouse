-- Metric layer objects (DESIGN.md §6). Applied by `uv run python -m omw.metrics.setup` and by every ad pipeline
-- (omw.metrics.campaign_keys) as the pipeline user.
-- marts.campaign_keys maps each platform's native campaign id to one cross-source campaign key. The pipelines
-- derive a key from the campaign name (origin = derived); `omw campaign-keys set` writes rows by hand (origin =
-- manual) that outrank derived ones. The compiler reads the latest row per (source, native_id) and falls back
-- to `<source>:<native_id>` when none exists.
CREATE TABLE IF NOT EXISTS marts.campaign_keys (
    source LowCardinality(String) COMMENT 'Source whose id this row maps: meta_ads, google_ads, linkedin_ads',
    native_id String COMMENT 'The campaign id as the source reports it',
    campaign_key String COMMENT 'Cross-source campaign key shared by every platform row of the same campaign, for example widgets_prospecting',
    campaign_name String COMMENT 'Human-readable campaign name for the key',
    origin LowCardinality(String) DEFAULT 'manual' COMMENT 'manual (set by hand, never overwritten) or derived (the slug of the campaign name, refreshed by every sync)',
    updated_at DateTime DEFAULT now() COMMENT 'When the mapping row was last written. The latest row per source and native id wins'
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (source, native_id);

-- Tables created before the origin column existed get it here (manual by default, as every row was then).
ALTER TABLE marts.campaign_keys ADD COLUMN IF NOT EXISTS origin LowCardinality(String) DEFAULT 'manual' COMMENT 'manual (set by hand, never overwritten) or derived (the slug of the campaign name, refreshed by every sync)' AFTER campaign_name;
