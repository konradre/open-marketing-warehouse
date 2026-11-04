# open-marketing-warehouse

An open-source data layer for marketing agents: scheduled ingestion pipelines, a warehouse, and a read-only SQL path for the agent.

**Status:** pre-build. The design lands in `DESIGN.md` before any code.

## Boundary

- The agent reads only from the warehouse, in SQL, through a `SELECT`-only user with row and time caps.
- The agent writes only through the platform APIs, with a narrow token per platform.
- The pipelines pull the platforms on a schedule, from one process. Pacing lives there.

## Planned stack

- Ingestion: [dlt](https://dlthub.com)
- Warehouse: [ClickHouse](https://clickhouse.com)
- Agent read path: [mcp-clickhouse](https://github.com/ClickHouse/mcp-clickhouse)
