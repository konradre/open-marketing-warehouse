-- Metric layer objects (DESIGN.md §6). Applied by `uv run python -m omw.metrics.setup` as the pipeline user.
-- marts.campaign_keys maps each platform's native campaign id to one cross-source campaign key. The compiler
-- uses the mapped key when a row exists and the source default (`<source>:<native_id>`) otherwise, so the
-- table only needs rows where campaigns on different platforms should share a key.
CREATE TABLE IF NOT EXISTS marts.campaign_keys (
    source LowCardinality(String) COMMENT 'Source whose id this row maps: meta_ads, google_ads, ...',
    native_id String COMMENT 'The campaign id as the source reports it',
    campaign_key String COMMENT 'Cross-source campaign key shared by every platform row of the same campaign, for example widgets_q4',
    campaign_name String COMMENT 'Human-readable campaign name for the key',
    updated_at DateTime DEFAULT now() COMMENT 'When the mapping row was last written'
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (source, native_id);
