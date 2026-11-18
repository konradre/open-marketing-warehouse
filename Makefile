# Day-to-day commands. `.env` is optional locally (compose has laptop defaults); CI copies .env.example.
ENV_FILE := $(wildcard .env)
COMPOSE  := docker compose $(if $(ENV_FILE),--env-file $(ENV_FILE)) -f deploy/docker-compose.yml

.PHONY: job scheduler-restart scheduler-smoke up down logs bootstrap chat observability profiles-smoke sync lint format typecheck test unit integration gate clean check campaign-keys eval-seed eval-report eval-drop demo demo-drop scheduler scheduler-build scheduler-logs schedule backup backup-list backup-verify backup-prune restore systemd-units

up: ## start ClickHouse and mcp-clickhouse, wait until healthy
	$(COMPOSE) up -d --wait clickhouse mcp-clickhouse

demo: up ## the quickstart: synthetic fixtures through the real pipelines (fixture mode), marts, checks, five answers
	uv run omw demo run $(DEMO_ARGS)

demo-drop: ## remove everything `make demo` loaded
	uv run omw demo drop

down: ## stop everything (all profiles); keeps volumes
	$(COMPOSE) --profile chat --profile observability --profile scheduler down

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

profiles-smoke: ## boot both optional profiles (needs the secrets in .env; deploy/profiles-env.sh writes throwaway ones) and prove they answer
	deploy/profiles-smoke.sh .env

# ---- running it on one host (docs/DEPLOY.md) ----
SCHEDULER := $(COMPOSE) --profile scheduler
IN_SCHEDULER := $(SCHEDULER) run --rm -T scheduler

scheduler-build: ## build the scheduler image from this checkout
	$(SCHEDULER) build scheduler

scheduler: scheduler-build ## start the scheduler (profile scheduler): every source on its cadence, checks, hook, backups
	$(SCHEDULER) up -d --wait clickhouse mcp-clickhouse scheduler

scheduler-logs:
	$(SCHEDULER) logs -f --tail 200 scheduler

scheduler-smoke: ## build the image and prove it on a fresh stack in fixture mode (what CI runs; loads demo data)
	deploy/scheduler-smoke.sh $(or $(ENV_FILE),.env.example)

scheduler-restart: ## restart the loop, for example after editing [schedule] in .dlt/config.toml
	$(SCHEDULER) restart scheduler

job: ## run one job now with its checks and failure hook: make job JOB=ga4
	@test -n "$(JOB)" || { echo "usage: make job JOB=<job>   (make schedule lists them)"; exit 2; }
	$(IN_SCHEDULER) sync $(JOB)

schedule: ## every job, its schedule, its next slot and how its last run ended
	$(IN_SCHEDULER) schedule list

backup: ## back up raw, marts and ops now
	$(IN_SCHEDULER) backup create

backup-list: ## every backup, its last restore drill, whether its file is still there
	$(IN_SCHEDULER) backup list

backup-verify: ## restore drill: the newest backup (or BACKUP=name.zip) into scratch databases
	$(IN_SCHEDULER) backup verify $(BACKUP)

backup-prune: ## delete all but the newest scheduled backups ([backup] keep)
	$(IN_SCHEDULER) backup prune

restore: ## replace raw, marts and ops with BACKUP=name.zip (stop the scheduler first; FORCE=1 to replace data)
	@test -n "$(BACKUP)" || { echo "usage: make restore BACKUP=omw-<time>.zip [FORCE=1]"; exit 2; }
	$(IN_SCHEDULER) backup restore $(BACKUP) $(if $(FORCE),--force)

systemd-units: ## render [schedule] as systemd timer units into deploy/systemd/
	uv run omw schedule systemd --output deploy/systemd

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

check: ## run the post-sync checks against the running warehouse (exit 1 on any failure)
	uv run omw check

campaign-keys: ## derive campaign keys from the loaded ad platforms
	uv run omw campaign-keys sync

eval-seed: ## load the synthetic evaluation warehouse (needs `make up`; refuses to replace data it did not seed)
	uv run omw eval seed

eval-report: ## run the offline planners through both arms and rewrite the results in eval/README.md
	uv run omw eval report

eval-drop: ## remove everything `make eval-seed` created
	uv run omw eval drop

clean: ## stop everything and delete volumes
	$(COMPOSE) --profile chat --profile observability --profile scheduler down -v
