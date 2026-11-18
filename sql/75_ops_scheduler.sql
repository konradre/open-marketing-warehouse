-- The scheduler's run log (DESIGN.md §4: ops holds sync state). One row per attempt of a job, written by
-- `omw sync` and the scheduler loop. The loop reads the latest slot per job from it, so a restart neither
-- repeats a finished slot nor forgets a missed one. Pipeline-only: the agent has no grant on it.
CREATE TABLE IF NOT EXISTS ops.sync_runs (
    started_at DateTime COMMENT 'When the job started, UTC',
    finished_at DateTime COMMENT 'When it ended, UTC',
    job LowCardinality(String) COMMENT 'Job name: search_console, meta_ads_daily, meta_ads_weekly, google_ads, ga4, stripe, linkedin_ads, twenty, backup, backup_verify',
    source LowCardinality(String) COMMENT 'Source the job loads, empty for the backup jobs',
    slot DateTime COMMENT 'The schedule slot this run answers, UTC (the start time for a run nothing scheduled)',
    trigger LowCardinality(String) COMMENT 'schedule (the scheduler loop), timer (a systemd timer) or manual',
    status LowCardinality(String) COMMENT 'ok, failed (the job raised), check_failed (it loaded but a post-sync check failed), crashed (its process died or ran out of time) or skipped (another job covered it)',
    rows UInt64 COMMENT 'Rows the sync normalised in its last pipeline run, or rows in the backup for a backup job',
    checks_failed UInt16 COMMENT 'Post-sync checks of the source that failed after this run',
    checks_warned UInt16 COMMENT 'Post-sync checks of the source that warned after this run',
    detail String COMMENT 'The error, the failed checks, or what the job did'
)
ENGINE = MergeTree
ORDER BY (job, started_at)
TTL started_at + INTERVAL 400 DAY;
