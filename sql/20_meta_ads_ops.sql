-- Pacing and token events from the Meta Ads sync (DESIGN.md §5: record why a run stopped).
-- Applied by omw.pipelines.meta_ads before it writes (CREATE IF NOT EXISTS, idempotent).
CREATE TABLE IF NOT EXISTS ops.pacing_events (
    recorded_at DateTime COMMENT 'When the pipeline wrote the event, UTC',
    source LowCardinality(String) COMMENT 'Always meta_ads for now',
    account_id String COMMENT 'Ad account id(s) of the run, comma separated, without act_',
    mode LowCardinality(String) COMMENT 'daily (7-day re-pull) or weekly (28-day reconcile)',
    event LowCardinality(String) COMMENT 'stopped: the pacer ended the run. slowed: it slept but finished. token_expiring, token_expired',
    reason LowCardinality(String) COMMENT 'utilisation, rate_limited, access_blocked or token',
    utilisation Float64 COMMENT 'Highest usage percentage seen across Meta usage headers at the decision',
    sleep_seconds Float64 COMMENT 'Total seconds the pacer slept during the run',
    detail String COMMENT 'Human-readable reason, with the header or Graph API error that decided it'
)
ENGINE = MergeTree
ORDER BY (source, account_id, recorded_at);
