# Day-to-day commands. `.env` is optional locally (compose has laptop defaults); CI copies .env.example.
ENV_FILE := $(wildcard .env)
COMPOSE  := docker compose $(if $(ENV_FILE),--env-file $(ENV_FILE)) -f deploy/docker-compose.yml

.PHONY: up down logs bootstrap chat observability sync lint format typecheck test unit integration gate clean

up: ## start ClickHouse and mcp-clickhouse, wait until healthy
	$(COMPOSE) up -d --wait clickhouse mcp-clickhouse

down: ## stop everything (all profiles); keeps volumes
	$(COMPOSE) --profile chat --profile observability down

logs:
	$(COMPOSE) logs -f clickhouse mcp-clickhouse

bootstrap: ## re-apply sql/00_*.sql to a running clickhouse (users, profile, quota, grants)
	$(COMPOSE) exec clickhouse /docker-entrypoint-initdb.d/00_omw_bootstrap.sh

CHAT_SECRETS := LIBRECHAT_JWT_SECRET LIBRECHAT_JWT_REFRESH_SECRET LIBRECHAT_CREDS_KEY LIBRECHAT_CREDS_IV
OBS_SECRETS  := LANGFUSE_NEXTAUTH_SECRET LANGFUSE_SALT LANGFUSE_ENCRYPTION_KEY LANGFUSE_POSTGRES_PASSWORD \
                LANGFUSE_REDIS_PASSWORD LANGFUSE_MINIO_PASSWORD LANGFUSE_CLICKHOUSE_PASSWORD

chat: ## also start LibreChat (profile chat); needs the LIBRECHAT_* secrets in .env
	deploy/require-env.sh .env $(CHAT_SECRETS)
	$(COMPOSE) --profile chat up -d --wait

observability: ## also start Langfuse (profile observability); needs the LANGFUSE_* secrets in .env
	deploy/require-env.sh .env $(OBS_SECRETS)
	$(COMPOSE) --profile observability up -d --wait

sync:
	uv sync --all-groups

lint:
	uv run ruff check . && uv run ruff format --check .

format:
	uv run ruff format . && uv run ruff check --fix .

typecheck:
	uv run mypy omw

unit:
	uv run pytest tests/unit

integration: ## needs `make up`
	OMW_REQUIRE_CLICKHOUSE=1 uv run pytest tests/integration

test: ## unit + integration (integration skips when ClickHouse is not reachable)
	uv run pytest

gate: ## the full gate from CLAUDE.md
	uv run ruff check . && uv run ruff format --check . && uv run mypy omw && uv run pytest

clean: ## stop everything and delete volumes
	$(COMPOSE) --profile chat --profile observability down -v
