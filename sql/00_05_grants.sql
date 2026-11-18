-- Grants (DESIGN.md §1, §7).
-- pipeline: owns raw and ops, and maintains the marts views and mapping tables.
GRANT ALL ON raw.* TO pipeline;
GRANT ALL ON ops.* TO pipeline;
GRANT ALL ON marts.* TO pipeline;
-- dlt discovers existing schemas through INFORMATION_SCHEMA before it loads.
GRANT SELECT ON INFORMATION_SCHEMA.* TO pipeline;
-- Backups (docs/DEPLOY.md): the restore drill restores the three databases under scratch names, and
-- `omw backup create` reads the size of what it wrote. ALL on the databases above already covers BACKUP.
GRANT ALL ON omw_drill_raw.* TO pipeline;
GRANT ALL ON omw_drill_marts.* TO pipeline;
GRANT ALL ON omw_drill_ops.* TO pipeline;
GRANT SELECT ON system.backups TO pipeline;

-- agent: SELECT on marts.* and the two ops verdict views only. No grant on raw, so it is invisible.
GRANT SELECT ON marts.* TO agent;
GRANT SELECT ON ops.freshness TO agent;
GRANT SELECT ON ops.check_verdicts TO agent;
