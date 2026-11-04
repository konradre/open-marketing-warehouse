---
name: clickhouse-agent-query-safety
description: Use when writing ClickHouse DDL, the agent user's profile, queries the agent or the metric compiler will run, or tests of those limits. Covers schema discovery, per-query and server-side limits, and table conventions for this repo.
---

# ClickHouse agent query safety

Adapted from ClickHouse Inc's `clickhouse-best-practices` agent skill (Apache-2.0,
github.com/ClickHouse/agent-skills), rules `agent-query-safety`, `agent-discovery-schema` and the schema rules.

## Server-side limits are the boundary

Per-query `SETTINGS` only help when the query remembers them. The `agent` user's settings profile is the
real limit, and every value is a constraint so a session cannot raise it:

| Setting | Value |
|---|---|
| `max_execution_time` | 30 |
| `timeout_before_checking_execution_speed` | 0 (makes the time limit wall-clock) |
| `max_result_rows` | 10000 |
| `result_overflow_mode` | `'break'` (partial result, rounded up to a block) |
| `max_rows_to_read` | 1e9 (the real scan guard; `LIMIT` alone does not stop a full scan) |
| `max_bytes_to_read` | 1e11 |
| `max_memory_usage` | set explicitly (self-hosted default is unlimited) |
| `max_bytes_before_external_group_by` / `_sort` | half of `max_memory_usage` (self-hosted default is no spill) |

Add a quota on queries and read bytes per hour: per-query limits do not stop many small queries.

Prove each limit in an integration test.

## Queries the compiler or the agent emits

- Always a `LIMIT` (default 1000) and a filter on the sort-key columns (`report`, `account_id`, `date`).
- Never `SELECT *` on a fact table.
- Explore progressively: count, then a 10-row sample, then the full query.
- On `TIMEOUT_EXCEEDED`: narrow the time range, add sort-key filters, check with `EXPLAIN ESTIMATE`.
- On `MEMORY_LIMIT_EXCEEDED`: narrow filters, lower `GROUP BY` cardinality.

## Discover before querying

List databases, then tables with `total_rows` and size, then columns with `comment`, then
`sorting_key`/`primary_key`. Never assume a column name. Column comments carry meaning, so every `marts`
column gets one.

## Table conventions

- Native types; `LowCardinality(String)` for low-cardinality text; avoid `Nullable` (use defaults).
- Plan the `ORDER BY` before creating the table; lead with the filter columns, low cardinality first.
- Start without partitioning; add it only for lifecycle needs.
- Prefer batch inserts; avoid mutations (`ALTER … UPDATE/DELETE`) for routine loads — replace whole
  slices instead; avoid `FINAL` in hot queries.
