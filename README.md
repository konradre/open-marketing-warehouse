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

### GA4

`omw.sources.ga4` runs the Data API `runReport` for four grains (`core` by day, `channels`, `landing_pages`,
`campaigns` by session campaign/source/medium), exposed as `marts.ga4_<grain>`. GA4 folds high-cardinality
values into an `(other)` row; the row is kept and flagged with `is_other` so the breakdowns still sum to
`core`. The daily run re-fetches the last 7 days (GA4 revises recent days) and replaces them;
`--days 400` does a first load.

Access: enable the *Google Analytics Data API* on a Google Cloud project, create a service account with a
JSON key, and add its email as a *Viewer* on each property (GA4 Admin > Property access management). Put the
key path and the numeric property ids in `.dlt/secrets.toml` under `[sources.ga4]`.

```bash
uv run python -m omw.pipelines.ga4 daily              # last 7 days
uv run python -m omw.pipelines.ga4 daily --days 400   # first load
```

### Stripe

`omw.sources.stripe` lists customers and subscriptions in full and merges them on id, re-fetches invoices
created in the last 90 days and merges them (they change until paid or voided), and appends balance
transactions incrementally (immutable). Attribution metadata (`campaign_key`, `utm_source`, `utm_medium`,
`utm_campaign`) is lifted from each object's `metadata`. `marts.stripe_revenue` is one row per paid invoice
with the campaign key resolved invoice → subscription → customer; `marts.stripe_revenue_daily` sums it by
day, currency and campaign key; `marts.stripe_subscriptions` lists subscriptions with their attribution.

Access: create a restricted API key (Developers > API keys) with read access to customers, subscriptions,
invoices and balance transactions, and put it in `.dlt/secrets.toml` under `[sources.stripe]`.

```bash
uv run python -m omw.pipelines.stripe daily
```

### LinkedIn Ads

`omw.sources.linkedin_ads` loads ad accounts, campaign groups (with their daily/total budgets and the
budget-optimisation flag), campaigns and creatives through dlt's declarative `rest_api` resources, and
daily campaign analytics (`adAnalytics`, pivot `CAMPAIGN`), exposed as `marts.linkedin_ads_campaigns` and
`marts.linkedin_ads_campaign_list`. The daily run re-fetches the last 7 days and replaces them. The
`LinkedIn-Version` header is pinned in `omw/sources/linkedin_ads/version.py`; every run warns three months
before LinkedIn retires that version and refuses to run after it.

**LinkedIn Ads access** (one-time): create an app in the LinkedIn Developer Portal, associate it with a
company page, and request the *Advertising API* product. LinkedIn reviews the application (this takes
days to weeks and needs a business justification); until it is approved the app cannot read ad data. Then
run the 3-legged OAuth flow once with the `r_ads` and `r_ads_reporting` scopes for a user who has at least
Viewer access on the ad accounts, keep the refresh token (valid for a year), and put the client id, secret,
refresh token and sponsored account ids in `.dlt/secrets.toml` under `[sources.linkedin_ads]`.

```bash
uv run python -m omw.pipelines.linkedin_ads daily    # re-fetch the last 7 days and replace them
```

### Twenty (CRM)

`omw.sources.twenty` loads companies, people and opportunities from a self-hosted Twenty through dlt's
declarative `rest_api` resources: cursor paging (`starting_after`), incremental on `updatedAt` (only
records changed since the last run), merged on id. `marts.twenty_pipeline` is one row per opportunity
with its stage, amount, company, point of contact and the campaign key resolved opportunity → person →
company; `marts.twenty_pipeline_by_stage` sums it by stage, currency and campaign key.

**Twenty access**: create an API key in Twenty (Settings > Developers > API keys) and put it with the
instance URL in `.dlt/secrets.toml` under `[sources.twenty]`. For attribution, add custom fields named
`campaignKey`, `utmSource`, `utmMedium` and `utmCampaign` (text) to the objects your sign-up flow writes
to; the pipeline reads them when present and leaves the columns empty otherwise.

```bash
uv run python -m omw.pipelines.twenty daily    # everything changed since the last run
```

## Post-sync checks

`omw check` runs after the syncs and exits non-zero on any failure, so a scheduler sees it:

- **over-count**: every breakdown grain (Search Console queries/pages/countries/devices, GA4 channels/landing
  pages/campaigns, Google ad groups, Meta ads) must not exceed its `core` grain by more than 1% on any
  group over the last 28 days;
- **freshness**: every source's last successful sync must be within its budget (`[checks.freshness_hours]`
  in `.dlt/config.toml`); a source that never synced, an empty sync log or an expired Meta token fails;
- **row-count sanity**: the last 7 days of each core grain against the 7 before: a collapse to zero fails,
  a swing beyond 3x warns.

Results go to `ops.check_results`; `ops.freshness` is the view the agent can read (one row per source
with the latest verdict).

```bash
uv run omw check                 # all sources
uv run omw check --source ga4    # one source
```

## Metric layer

`SEMANTICS.md` says what every measure means. `metrics/*.yaml` defines the metrics (one report grain per
`marts` view, one measure per entry, derived cross-source measures such as `roas` and `cac`), validated by
the pydantic schema in `omw/metrics/schema.py` on every test run. `omw.metrics.server` is the
`query_metrics` MCP server (stdio): `list_metrics`, `list_dimensions` and `query_metrics(metrics,
dimensions, filters, time_range, compare)`. It compiles one `SELECT` per request (one CTE per source for
cross-source measures, joined on the common grain), checks it is a single `SELECT`/`WITH`, runs it as the
`agent` user, and answers with a mood: `serve`, `disclose` (with caveats), `clarify` (with candidates) or
`refuse` (with the reason).

```bash
uv run python -m omw.metrics.setup     # once: creates marts.campaign_keys (the cross-platform campaign key mapping)
uv run python -m omw.metrics.server    # the MCP server on stdio; OMW_CLICKHOUSE_HOST / OMW_AGENT_PASSWORD from the environment
```

## Layout

```
omw/            Python package: sources/, pacing/ (Meta header-aware pacer), pipelines/, checks/, metrics/ (schema, compiler, MCP server)
metrics/        metric definitions (YAML), validated by omw/metrics/schema.py
SEMANTICS.md    business semantics for the agent
sql/            ClickHouse DDL; 00_* is the bootstrap (databases, users, profile, quota, grants), NN_<source>_* marts views
deploy/         docker-compose.yml, ClickHouse config and init script, LibreChat config
tests/          unit/ (no services), integration/ (ClickHouse + mcp-clickhouse), fixtures/<source>/
```
