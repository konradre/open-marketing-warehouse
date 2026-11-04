-- Per-hour quota for the agent (DESIGN.md §7): per-query limits do not stop many small queries.
-- Values are a first cut (DESIGN.md §10 leaves the final caps open).
CREATE QUOTA OR REPLACE agent_quota
    KEYED BY user_name
    FOR INTERVAL 1 hour MAX queries = 1000, read_bytes = 1000000000000
TO agent;
