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

## Sources

### Search Console

`omw.sources.search_console` pulls `searchanalytics.query` into one table per grain (`core`, `queries`,
`pages`, `query_page`, `countries`, `devices`) and exposes them as `marts.search_console_<grain>` views.

**Search Console access** (one-time, per property):

1. In Google Cloud, enable the *Google Search Console API* on a project and create a service account with
   a JSON key (IAM & Admin > Service accounts > Keys). Save the file outside the repo.
2. In Search Console, open the property > Settings > Users and permissions > Add user, and add the service
   account's email (`...@...iam.gserviceaccount.com`) with *Full* or *Restricted* permission. Without this
   step the API answers 403 and the sync stops with a message naming the property.
3. Put the key path and the properties in `.dlt/secrets.toml` under `[sources.search_console]`
   (`site_urls` exactly as Search Console names them: `sc-domain:example.com` or `https://example.com/`).

Then:

```bash
uv run python -m omw.pipelines.search_console backfill   # the 16 months Google keeps, oldest first, 90-day chunks
uv run python -m omw.pipelines.search_console daily      # re-fetch the last 10 days and replace them
```

The backfill is resumable: each chunk is its own load, a failed chunk stops the run and keeps the completed
ones, and the next run continues from the last completed chunk (progress lives in the dlt state, next to
the data). The daily sync replaces the whole `(property, search type, day)` slice it fetched, so Google's
2–3 day restatement never leaves duplicates. A daily run that loads no `core` rows fails.

### Meta Ads

`omw.sources.meta_ads` pulls campaigns, ad sets and ads (merged on id) and daily insights at campaign level
(and at ad level for live ad sets, with `--level ad`), exposed as `marts.meta_ads_campaigns` and
`marts.meta_ads_ads`. Every API call goes through the pacer in `omw/pacing/`: it reads Meta's usage headers,
slows down above 75% utilisation, and ends the run cleanly near 100% or on a rate-limit error, writing the
reason to `ops.pacing_events` instead of retrying into the limit.

Setup: create a system user in Meta Business Settings, give it `ads_read` on the ad accounts, generate a
token, and put the token, the account ids (without `act_`) and the token's expiry date in
`.dlt/secrets.toml` under `[sources.meta_ads]`. The sync warns 7 days before the expiry and refuses to run
after it.

```bash
uv run python -m omw.pipelines.meta_ads daily              # re-fetch the last 7 days and replace them
uv run python -m omw.pipelines.meta_ads weekly             # reconcile the last 28 days (Meta restates that far back)
uv run python -m omw.pipelines.meta_ads daily --level ad   # ad-level insights for live ad sets
```

### Google Ads

`omw.sources.google_ads` runs GAQL through the REST `searchStream` endpoint: daily campaign and ad group
performance (impressions, clicks, cost, conversions, conversion value) plus the campaign list, exposed as
`marts.google_ads_campaigns`, `marts.google_ads_ad_groups` and `marts.google_ads_campaign_list`. Google
attributes conversions for up to 30 days, so the daily run re-fetches the last 30 days and replaces them.
`cost_micros` is kept as Google reports it; the views divide it into a `Decimal` amount in the account
currency.

**Google Ads access** (one-time):

1. In the manager (MCC) account, apply for a developer token under Tools > API Center (test-account access
   is immediate; production access needs Google's approval).
2. In Google Cloud, enable the Google Ads API and create an OAuth client (desktop or web). Run the OAuth
   consent once for a user with access to the accounts and keep the refresh token.
3. Put the developer token, client id and secret, refresh token, the manager account id
   (`login_customer_id`) and the client account ids (`customer_ids`, digits only) in `.dlt/secrets.toml`
   under `[sources.google_ads]`. A customer the token cannot see fails the run with Google's
   `authorizationError` in the message.

```bash
uv run python -m omw.pipelines.google_ads daily    # re-fetch the last 30 days and replace them
```

The API version is pinned in `omw/sources/google_ads/client.py` (`api_version` in config overrides it);
Google sunsets each version about a year after release.

## Layout

```
omw/            Python package: sources/, pacing/ (Meta header-aware pacer), pipelines/, checks/, metrics/
sql/            ClickHouse DDL; 00_* is the bootstrap (databases, users, profile, quota, grants), 10_* marts views
deploy/         docker-compose.yml, ClickHouse config and init script, LibreChat config
tests/          unit/ (no services), integration/ (ClickHouse + mcp-clickhouse), fixtures/<source>/
```
