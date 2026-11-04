"""Values the skeleton promises (DESIGN.md §7), shared by unit and integration tests."""

# The agent's settings profile: every entry is declared CONST in sql/00_03_profile_agent.sql.
AGENT_PROFILE_SETTINGS: dict[str, str] = {
    "readonly": "1",
    "max_execution_time": "30",
    "timeout_before_checking_execution_speed": "0",
    "max_result_rows": "10000",
    "result_overflow_mode": "break",
    "max_rows_to_read": "1000000000",
    "max_bytes_to_read": "100000000000",
    "max_memory_usage": "4000000000",
    "max_bytes_before_external_group_by": "2000000000",
    "max_bytes_before_external_sort": "2000000000",
}

# A value for each setting that would loosen the cap; the agent must not be able to apply any of them.
AGENT_PROFILE_RAISED: dict[str, str] = {
    "readonly": "0",
    "max_execution_time": "300",
    "timeout_before_checking_execution_speed": "10",
    "max_result_rows": "100000",
    "result_overflow_mode": "throw",
    "max_rows_to_read": "10000000000",
    "max_bytes_to_read": "1000000000000",
    "max_memory_usage": "8000000000",
    "max_bytes_before_external_group_by": "4000000000",
    "max_bytes_before_external_sort": "4000000000",
}

AGENT_QUOTA = {"queries": 1000, "read_bytes": 1_000_000_000_000, "duration_seconds": 3600}

DATABASES = ("raw", "marts", "ops")
