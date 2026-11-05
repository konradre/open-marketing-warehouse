-- Post-sync check results (DESIGN.md §7) and the agent's freshness view. Applied by `omw check`.
CREATE TABLE IF NOT EXISTS ops.check_results (
    checked_at DateTime COMMENT 'When the check ran, UTC',
    source LowCardinality(String) COMMENT 'Source the check concerns: search_console, meta_ads, google_ads, ga4, stripe',
    check LowCardinality(String) COMMENT 'overcount, freshness or rowcount',
    report LowCardinality(String) COMMENT 'Report grain the check looked at, or sync for freshness',
    status LowCardinality(String) COMMENT 'pass, warn or fail',
    observed Float64 COMMENT 'What was measured: max breakdown/core ratio, hours since the last sync, rows in the current window',
    expected Float64 COMMENT 'The bound: 1 + tolerance, the freshness budget in hours, rows in the previous window',
    detail String COMMENT 'Human-readable outcome'
)
ENGINE = MergeTree
ORDER BY (source, check, report, checked_at);

-- The agent may read two ops objects (grants in sql/00_05_grants.sql): the latest freshness verdict per source,
-- and the latest verdict of every check, which the metric server turns into caveats.
CREATE OR REPLACE VIEW ops.freshness (
    source LowCardinality(String) COMMENT 'Source: search_console, meta_ads, google_ads, ga4, stripe',
    status LowCardinality(String) COMMENT 'pass when the last successful sync is within budget, fail otherwise (stale, never synced, or an expired token)',
    age_hours Float64 COMMENT 'Hours between the last successful sync and the check',
    budget_hours Float64 COMMENT 'The freshness budget for the source',
    checked_at DateTime COMMENT 'When the verdict was produced, UTC. Stale verdicts mean the checks themselves are not running',
    detail String COMMENT 'Human-readable reason'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, status, age_hours, budget_hours, last_checked_at AS checked_at, detail
FROM (
    SELECT source, argMax(status, checked_at) AS status, argMax(observed, checked_at) AS age_hours,
           argMax(expected, checked_at) AS budget_hours, max(checked_at) AS last_checked_at,
           argMax(detail, checked_at) AS detail
    FROM ops.check_results
    WHERE check = 'freshness'
    GROUP BY source
);

CREATE OR REPLACE VIEW ops.check_verdicts (
    source LowCardinality(String) COMMENT 'Source the check concerns',
    check LowCardinality(String) COMMENT 'overcount, freshness or rowcount',
    report LowCardinality(String) COMMENT 'Report grain the check looked at, or sync for freshness',
    status LowCardinality(String) COMMENT 'pass, warn or fail at the latest run of this check',
    observed Float64 COMMENT 'What the latest run measured',
    expected Float64 COMMENT 'The bound the latest run compared against',
    checked_at DateTime COMMENT 'When the latest run of this check happened, UTC',
    detail String COMMENT 'Human-readable outcome of the latest run'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, check, report, status, observed, expected, last_checked_at AS checked_at, detail
FROM (
    SELECT source, check, report, argMax(status, checked_at) AS status, argMax(observed, checked_at) AS observed,
           argMax(expected, checked_at) AS expected, max(checked_at) AS last_checked_at,
           argMax(detail, checked_at) AS detail
    FROM ops.check_results
    GROUP BY source, check, report
);
