# open-marketing-warehouse

An open-source data layer for marketing agents: scheduled ingestion pipelines, a ClickHouse warehouse, and a
read-only SQL path for the agent. `DESIGN.md` is the source of truth; `docs/THREADS.md` lists the build
threads.

**Status:** skeleton in place (T1). Sources, checks and the metric layer land in T2–T9.

## Boundary

- The agent reads only from the warehouse, in SQL, through a `SELECT`-only user with row, time, memory and
  per-hour caps that it cannot raise (`sql/00_*.sql`, proven by `tests/integration/`).
- The agent writes only through the platform APIs, with a narrow token per platform. Out of scope here.
- The pipelines pull the platforms on a schedule, from one process. Pacing lives there.

## Stack

- Ingestion: [dlt](https://dlthub.com)
- Warehouse: [ClickHouse](https://clickhouse.com)
- Agent read path: [mcp-clickhouse](https://github.com/ClickHouse/mcp-clickhouse), connected as the `agent` user
- Optional, off by default: LibreChat (`chat` profile), Langfuse (`observability` profile)

## Quick start

Needs Docker (with Compose v2) and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env            # laptop defaults; change the passwords for anything shared
cp .dlt/secrets.toml.example .dlt/secrets.toml
uv sync --all-groups
make up                         # ClickHouse on 127.0.0.1:8123/9000, mcp-clickhouse on 127.0.0.1:8000/mcp
make test                       # unit + integration tests against the running stack
```

On first start the ClickHouse container runs `sql/00_*.sql`: the `raw`, `marts` and `ops` databases, the
`pipeline` and `agent` users, the agent's settings profile, quota and grants. After editing those files on a
running container, `make bootstrap` re-applies them.

`make gate` runs what CI runs: `ruff`, `mypy`, unit tests, then the integration tests with the compose stack.

Optional UIs need their secrets in `.env` (see the comments in `.env.example`):

```bash
make chat                       # LibreChat on 127.0.0.1:3080, with the warehouse MCP server wired in
make observability              # Langfuse on 127.0.0.1:3000
```

## Layout

```
omw/            Python package: sources/, pacing/, pipelines/, checks/, metrics/
sql/            ClickHouse DDL; 00_* is the bootstrap (databases, users, profile, quota, grants)
deploy/         docker-compose.yml, ClickHouse config and init script, LibreChat config
tests/          unit/ (no services), integration/ (ClickHouse + mcp-clickhouse), fixtures/<source>/
```
