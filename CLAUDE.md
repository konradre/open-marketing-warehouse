# CLAUDE.md — working rules for agents in this repo

Read `DESIGN.md` first. It is the source of truth. Your thread brief is in `docs/THREADS.md`.

## Work autonomously
- Do the whole thread without stopping to ask. Make reasonable choices, record them in the PR description under
  "Decisions", and keep going.
- Ask only if the brief contradicts `DESIGN.md` in a way you cannot resolve. Otherwise follow `DESIGN.md`.

## Git
- One branch per thread: `t<N>-<short-name>` (for example `t3-meta-pacing`). Never push to `main`; it is protected.
- Open one pull request per thread against `main` (REST: `gh api repos/{owner}/{repo}/pulls -f ...`; the cloud
  proxy rejects `gh pr`, which uses GraphQL).
- Merge your own PR when the full gate is green and the branch is rebased on the latest `main`: squash-merge
  through REST (`gh api -X PUT repos/{owner}/{repo}/pulls/<n>/merge -f merge_method=squash`). If CI exists on
  `main`, wait for it to pass on the PR first. Never force-push `main`; never merge another thread's PR.
- Conventional Commits: `feat|fix|refactor|build|ci|chore|docs|test`.
- Before opening the PR, rebase on the latest `main` and resolve conflicts in files you touched.
- Touch only what your thread owns. `pyproject.toml`, `deploy/` and `.github/` belong to T1: add your own
  dependencies to `pyproject.toml` and nothing else there.

## Code
- Python 3.12, managed with `uv`. Format and lint with `ruff`, type-check with `mypy`, test with `pytest`.
- Keep files under about 500 lines; split by responsibility.
- Follow the layout in `DESIGN.md` §8 and the data model in §4.
- ClickHouse work: follow `.claude/skills/clickhouse-agent-query-safety`. Custom dlt sources: follow
  `.claude/skills/dlt-custom-source`.

## Tests
- Never call a live platform API. Use recorded JSON fixtures under `tests/fixtures/<source>/` with synthetic
  identifiers and amounts.
- Integration tests run ClickHouse in Docker (`docker compose -f deploy/docker-compose.yml up -d clickhouse`).
- A test that fails reveals a bug: fix the code, never weaken the assertion.
- Run the full gate before the PR: `uv run ruff check . && uv run mypy omw && uv run pytest`.

## Never commit
- Secrets, tokens, real account IDs, real customer data, `.dlt/secrets.toml`, `.env`.
- Local machine paths, private hostnames or IP addresses.
- Ship `*.example` files that document every key instead.

## Licence
- This repo is Apache-2.0. Re-author patterns in your own code. Do not paste code or text from projects whose
  licence is not Apache-2.0, MIT or BSD. Credit any Apache-2.0 or MIT source you adapt in `NOTICE`.
