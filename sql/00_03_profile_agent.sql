-- The agent's settings profile (DESIGN.md §7). Every limit is a CONST constraint, so neither SET nor a
-- client-sent setting can raise it. readonly = 1 also rejects any settings change the client tries.
-- max_memory_usage is explicit because the self-hosted default is unlimited; the external group-by and sort
-- thresholds are half of it so big aggregations spill to disk instead of failing.
CREATE SETTINGS PROFILE OR REPLACE agent_profile SETTINGS
    readonly = 1 CONST,
    max_execution_time = 30 CONST,
    timeout_before_checking_execution_speed = 0 CONST,
    max_result_rows = 10000 CONST,
    result_overflow_mode = 'break' CONST,
    max_rows_to_read = 1000000000 CONST,
    max_bytes_to_read = 100000000000 CONST,
    max_memory_usage = 4000000000 CONST,
    max_bytes_before_external_group_by = 2000000000 CONST,
    max_bytes_before_external_sort = 2000000000 CONST
TO agent;
