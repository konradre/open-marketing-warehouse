-- Grants (DESIGN.md §1, §7).
-- pipeline: owns raw and ops, and maintains the marts views and mapping tables.
GRANT ALL ON raw.* TO pipeline;
GRANT ALL ON ops.* TO pipeline;
GRANT ALL ON marts.* TO pipeline;

-- agent: SELECT on marts.* and ops.freshness only. No grant on raw, so it is invisible.
GRANT SELECT ON marts.* TO agent;
GRANT SELECT ON ops.freshness TO agent;
