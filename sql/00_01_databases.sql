-- Databases (DESIGN.md §4).
--   raw   what dlt loads; pipeline-owned, invisible to the agent
--   marts one view or table per report grain; the only database the agent reads
--   ops   sync state, check results, freshness; the agent reads ops.freshness only
CREATE DATABASE IF NOT EXISTS raw COMMENT 'dlt loads. Pipeline-owned. The agent cannot read it.';
CREATE DATABASE IF NOT EXISTS marts COMMENT 'One view or table per report grain. The only database the agent reads.';
CREATE DATABASE IF NOT EXISTS ops COMMENT 'Sync state, check results, freshness. The agent reads ops.freshness only.';
