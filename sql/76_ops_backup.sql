-- Backup bookkeeping (docs/DEPLOY.md "Backup and restore"). Applied by `omw backup`. Pipeline-only.
-- The manifest is written just before the BACKUP statement, so every backup carries the row counts it must
-- restore to: the restore drill compares against the manifest inside the backup itself.
CREATE TABLE IF NOT EXISTS ops.backup_manifest (
    backup String COMMENT 'Backup file name on the backups disk, for example omw-20261118T060000Z.zip',
    taken_at DateTime COMMENT 'When the counts were taken, UTC, just before the backup started',
    database_name LowCardinality(String) COMMENT 'raw, marts or ops',
    table_name String COMMENT 'Table or view name',
    kind LowCardinality(String) COMMENT 'table or view',
    rows UInt64 COMMENT 'count() of the table when the backup started, 0 for a view'
)
ENGINE = MergeTree
ORDER BY (backup, database_name, table_name)
TTL taken_at + INTERVAL 400 DAY;

CREATE TABLE IF NOT EXISTS ops.backup_log (
    at DateTime COMMENT 'When it happened, UTC',
    backup String COMMENT 'Backup file name on the backups disk',
    event LowCardinality(String) COMMENT 'created, failed, verified (the restore drill matched the manifest), verify_failed, restored or pruned',
    tables UInt32 COMMENT 'Tables and views the event covered',
    rows UInt64 COMMENT 'Rows the event covered',
    bytes UInt64 COMMENT 'Size of the backup file for created, when the server reports it',
    detail String COMMENT 'What went wrong, or a short description'
)
ENGINE = MergeTree
ORDER BY (backup, at)
TTL at + INTERVAL 400 DAY;
