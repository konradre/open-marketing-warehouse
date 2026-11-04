# Build threads

One brief per thread. Each thread: read `CLAUDE.md` and `DESIGN.md`, work on branch `t<N>-<name>`, open one PR.
**Order:** run T1 first and let it merge (it creates `pyproject.toml`, compose and CI). Then run T2–T9 in
parallel, each starting from the `main` that includes T1. Definition of done for
every thread: code + tests with recorded fixtures + the full gate green + a PR whose description lists
"Decisions" and "Follow-ups".

---

## T1 — Skeleton (`t1-skeleton`)

Owns `pyproject.toml`, `deploy/`, `.github/`, `sql/00_*`.

- `pyproject.toml` (uv, Python 3.12): `dlt[clickhouse]`, `clickhouse-connect`, `mcp`, `pydantic`, `pyyaml`;
  dev: `ruff`, `mypy`, `pytest`. Package `omw/` with empty subpackages per `DESIGN.md` §8.
- `deploy/docker-compose.yml`: `clickhouse` (pinned image, healthcheck, named volume), `mcp-clickhouse`
  connected as the `agent` user, and profiles `chat` (LibreChat) and `observability` (Langfuse), both off by
  default. Bind ports to localhost only.
- `sql/`: databases `raw`, `marts`, `ops`; users `pipeline` (write `raw`, `ops`) and `agent`; the agent
  settings profile, constraints and quota exactly as `DESIGN.md` §7.
- Integration tests that prove: `agent` cannot write; cannot read `raw`; cannot raise any capped setting with
  `SET` or a query-level setting; a big result breaks at `max_result_rows`; `mcp-clickhouse` still runs queries
  as `agent`.
- `.github/workflows/ci.yml`: ruff, mypy, unit tests, integration tests with the ClickHouse service.
- `.dlt/secrets.toml.example`, `.env.example`, `Makefile` (`make up`, `make test`, `make gate`).

## T2 — Search Console source (`t2-search-console`)

- dlt source over the Search Console API (`searchanalytics.query`), grains `core` (date), `queries`,
  `pages`, `query_page`, `countries`, `devices`; dimension `searchType` configurable.
- Pagination with `startRow` and `rowLimit`; data lag of about 2–3 days handled by the rolling window.
- **Backfill** of the full 16 months, oldest first, in 90-day chunks; stop at the first failed chunk, keep the
  completed ones, resume from state.
- Daily sync re-fetches the last 10 days and replaces that slice (`DESIGN.md` §4).
- Auth: service account JSON path from secrets; document the property-access step in the README.
- `marts` views per grain with column comments.

## T3 — Meta pacing + cadences (`t3-meta-pacing`)

- Use dlt `facebook_ads` for entities and insights; wrap its HTTP session with the pacer in `omw/pacing/`.
- The pacer reads `x-fb-ads-insights-throttle` and the three usage headers, takes the max, sleeps in proportion
  above ~75%, stops the run cleanly near 100% and on error 4, and records the stop reason in `ops`
  (`DESIGN.md` §5).
- Cadences: daily 7-day re-pull, weekly 28-day reconcile; level `campaign` by default, `ad` configurable.
- Unit tests drive the pacer with recorded header sequences (rising usage, error 4, recovery).

## T4 — Google Ads reports (`t4-google-ads`)

- Custom dlt resources running GAQL through `GoogleAdsService.SearchStream` (REST): `campaign` and
  `ad_group` daily performance (impressions, clicks, cost_micros, conversions, conversions_value), plus
  `campaign` entities.
- `cost_micros` → currency amounts in `marts` with the account currency.
- Rolling 30-day re-pull for conversion lag; replace the slice.
- Fixtures for paginated streams and partial-failure responses.

## T5 — GA4 grains + Stripe (`t5-ga4-stripe`)

- GA4 through dlt `google_analytics`: define report grains `core` (date), `channels`
  (sessionDefaultChannelGroup), `landing_pages`, `campaigns` (sessionCampaignName, source/medium); handle the
  `(other)` row.
- Stripe through dlt `stripe_analytics`: incremental for immutable objects; merge for subscriptions, customers,
  invoices. A `marts` revenue view by date and by campaign key where attribution metadata exists.

## T6 — Metric layer (`t6-metric-layer`)

- `SEMANTICS.md` per `DESIGN.md` §6: every planned measure, unit, grain, window and known traps.
- `metrics/*.yaml` + a pydantic schema; validation in CI.
- `omw/metrics/`: compiler from a metric request to ClickHouse SQL; per-source pre-aggregation CTEs to the
  shared grain for derived cross-source measures (ROAS, CAC); the single-statement `SELECT`/`WITH` guard.
- MCP server `query_metrics` (official MCP Python SDK): `list_metrics`, `list_dimensions`, `query_metrics`
  with `compare` (previous period, week, month, year). Every response carries a mood: serve / disclose /
  clarify / refuse, with a reason and caveats as structured fields.
- Tests for each mood, including: a metric at a grain it lacks → refuse; a non-additive measure summed →
  refuse; a dimension on one side of a cross-source join → refuse; data inside Meta's restatement window →
  disclose.

## T7 — Post-sync checks (`t7-checks`)

- `omw/checks/`: over-count check (breakdown grain ≤ `core` + 1%), freshness per source against a budget,
  row-count sanity against the previous window. Results to `ops.check_results`; `ops.freshness` view for the
  agent.
- A CLI `omw check` that exits non-zero on failure, for the scheduler.

## T8 — LinkedIn Ads source (`t8-linkedin-ads`)

- `rest_api`-based dlt source: ad accounts, campaign groups (including `dailyBudget` and budget optimisation
  fields), campaigns, creatives, and `adAnalytics` daily by campaign.
- Pin `LinkedIn-Version` (YYYYMM) in one constant; warn at start when it is within 3 months of LinkedIn's
  12-month support window and fail when past it.
- OAuth refresh-token flow from secrets; document the app-approval prerequisite.

## T9 — Twenty source (`t9-twenty`)

- `rest_api`-based dlt source over Twenty's REST API (self-hosted base URL from config): companies, people,
  opportunities, with cursor pagination and `updatedAt` incrementals.
- A `marts` pipeline view by stage and by source/campaign key.
- Fixtures shaped from Twenty's public API docs.
