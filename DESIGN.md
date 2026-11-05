# DESIGN — open-marketing-warehouse

An open-source data layer for a marketing agent. Scheduled pipelines pull the ad, analytics, CRM and billing
platforms into a warehouse. The agent reads that warehouse in SQL, and through a thin metric layer that knows
what each number means. It never calls a platform API to answer a question.

Status: **design, build starting.** Section 9 lists the work threads; `docs/THREADS.md` holds one brief each.

---

## 1. The boundary

- **The agent reads only from the warehouse.** Through a `SELECT`-only ClickHouse user, and through the
  `query_metrics` tool (§6).
- **The agent writes only through platform APIs**, with a narrow token per platform. That surface is out of
  scope for this repo.
- **The pipelines pull the platforms on a schedule, from one process.** Pacing lives there, never in the agent.

The boundary is a credential, not an instruction. The agent's warehouse user cannot write, cannot raise its own
limits, and cannot see the raw tables or the pipeline state.

**Sources in scope:** Meta Ads, Google Ads, GA4, Google Search Console, LinkedIn Ads, Twenty (CRM) and Stripe.

---

## 2. Components

| Layer | Pick | Notes |
|---|---|---|
| Ingestion | [dlt](https://dlthub.com) (Apache-2.0) | Pipelines as Python. Verified sources exist for Meta (`facebook_ads`), GA4 (`google_analytics`) and Stripe (`stripe_analytics`); a generic `rest_api` source for the rest. |
| Warehouse | ClickHouse, single node (Apache-2.0) | Syncs write while the agent reads. |
| Agent SQL path | [mcp-clickhouse](https://github.com/ClickHouse/mcp-clickhouse) as the `agent` user | Tools: `list_databases`, `list_tables`, `run_query`. |
| Agent metric path | `query_metrics` MCP server (ours, §6) | Metric definitions, grain checks, answer moods. |
| Scheduling | cron / systemd timers, or a container loop | No orchestrator until transforms depend on each other. |
| Transforms | SQL views in `marts` | dbt later, if models start to depend on each other. |
| Optional | LibreChat (chat UI), Langfuse (agent traces) | Compose profiles `chat` and `observability`. Off by default. |

---

## 3. Source coverage

| Source | Starting point | Gap to build |
|---|---|---|
| Meta Ads | dlt `facebook_ads` (entities + async insights, incremental) | Header-aware pacing (§5); two cadences |
| Google Ads | dlt `google_ads` is a stub (no spend, clicks or conversions) | Custom GAQL reports through `search_stream` |
| GA4 | dlt `google_analytics` (Data API reports) | Named report grains (§4) |
| Search Console | none | Full source; **chunked backfill of the 16 months Google keeps** |
| LinkedIn Ads | none | Full source over the REST API; pin `LinkedIn-Version` (YYYYMM) in one place and warn before it sunsets |
| Twenty (CRM) | none | Full source over Twenty's API |
| Stripe | dlt `stripe_analytics` (incremental for immutable objects only) | Merge or full reload for mutable objects |

---

## 4. Data model

**Databases:**
- `raw` — what dlt loads. Pipeline-owned. The agent cannot read it.
- `marts` — one view or table per **report grain**. The only database the agent reads.
- `ops` — sync state, check results, freshness. The agent can read `ops.freshness` only.

**Report grains, stored side by side, never summed together.** Each source has named grains (`core`,
`campaigns`, `queries`, `pages`, …). Every row carries `source` and `report`; a query filters on exactly one
report. A breakdown grain may undercount `core` (anonymised queries, GA4's `(other)` bucket), never overcount it.

**Restatement.** Platforms revise numbers after the fact (Meta for up to 28 days). Each sync re-fetches a
rolling window and **replaces that `(source, report, window)` slice atomically** — never appends a duplicate.

**Backfill.** Oldest first, in 90-day chunks. Stop at the first failed chunk and keep the completed ones.

**ClickHouse conventions:** native types, `LowCardinality(String)` for low-cardinality text, avoid
`Nullable`, `ORDER BY` led by the columns the agent filters on (`report`, `account_id`, `date`), no partitioning
until a table needs it. Every `marts` column carries a `COMMENT`; the agent reads them.

---

## 5. Pacing the Meta pull

dlt's `facebook_ads` retries **after** a rate-limit error (12 attempts, backoff ×2) and never reads the usage
headers, so it cannot slow down before the limit.

**Build a header-aware pacing layer in front of it:**
- On every response read **`x-fb-ads-insights-throttle`** (`app_id_util_pct`, `acc_id_util_pct`, access tier)
  **and** `x-ad-account-usage`, `x-app-usage`, `x-business-use-case-usage`. Take the highest utilisation.
- Above about 75%, sleep in proportion to the excess. Near 100%, end the run cleanly and record why in `ops`.
- At the limit Meta returns **error 4**; treat it as a stop, not a retry storm.
- New apps start in the tightest tier, so pacing must work at low quotas.

**Two cadences:** daily, a 7-day re-pull; weekly, a 28-day reconcile. A daily 28-day window costs about 28
async jobs per account per day.

**Level:** `campaign` by default; `ad` for live ad sets only. Configurable.

**Token:** a system-user token. Record its expiry if known, and fail the freshness check (§7) rather than going
silently stale.

---

## 6. The metric layer

A plain document of business semantics, given to the model beside the schema, raised text-to-SQL accuracy by 17
to 23 points across three frontier models on ClickHouse, from roughly 48% to 68%
([arXiv 2604.25149](https://arxiv.org/abs/2604.25149)). Model choice within the tier did not matter; the
document did. Correct SQL can still return the wrong business number, so the layer also checks what it is
allowed to answer.

**Three parts:**

1. **`SEMANTICS.md`** (repo root, served to the agent). Each measure in plain words: definition, unit, grain,
   attribution window, known traps (Meta restatement, GA4 `(other)`, Search Console anonymised queries,
   currency). Kept under about 6 KB.

2. **Metric definitions** — `metrics/*.yaml`, validated by a schema:
   ```yaml
   - name: spend
     source: meta_ads
     report: campaigns
     expr: sum(spend)
     unit: currency
     grains: [date, account_id, campaign_id]
     additive: true
   - name: roas
     derived: revenue / spend          # combines two measures, never two tables in one scan
     requires_common_grain: [date, campaign_key]
   ```

3. **`query_metrics` MCP server** (Python, the official MCP SDK). Tools: `list_metrics`, `list_dimensions`,
   `query_metrics(metrics, dimensions, filters, time_range, compare)`. Rates are computed at query time, never
   stored. Every answer carries one **mood**:
   - `serve` — answered, no material condition.
   - `disclose` — answered, with a caveat that travels with the numbers (an approximate distinct count, data
     inside Meta's 28-day restatement window, a source past its freshness budget).
   - `clarify` — more than one valid meaning; return the candidates, not a guess.
   - `refuse` — no valid path: a metric asked at a grain it does not have, a non-additive measure summed across
     a dimension, a filter that would fan out a join. Return the reason.

**Cross-source metrics (ROAS, CAC).** Joining ad spend to Stripe or CRM revenue is where fan-out and chasm traps
happen. Aggregate **each source separately to the shared grain first**, one CTE per source, then join the
aggregates on the common keys and combine them as a derived measure. Refuse when a requested dimension or filter
exists on only one side or would turn a join one-to-many. Campaign keys across platforms come from a mapping
table in `marts`, maintained by the pipeline.

**Second guard in code.** The server compiles SQL itself and runs it as the `agent` user; it also asserts a
single `SELECT`/`WITH` statement before execution. The server-side profile (§7) is the real limit.

---

## 7. Safety and operations

**The `agent` ClickHouse user:**
- Grants: `SELECT` on `marts.*` and the check verdict views `ops.freshness` and `ops.check_verdicts` only.
- A settings profile with `readonly = 1`, and every limit below declared as a constraint (`READONLY` / `CONST`)
  so neither a `SET` nor a client-sent setting can raise it. Verify the profile still works with
  `mcp-clickhouse`, which sends its own `readonly` setting per query:
  `max_execution_time = 30`, `timeout_before_checking_execution_speed = 0`, `max_result_rows = 10000`,
  `result_overflow_mode = 'break'`, `max_rows_to_read = 1e9`, `max_bytes_to_read = 1e11`,
  `max_memory_usage`, `max_bytes_before_external_group_by`.
- A quota on queries and read bytes per hour.
- Tests prove each limit holds: a `SET` that raises a cap fails, a write fails, `raw` is invisible.

`mcp-clickhouse` forces `readonly=1` client-side and respects a stricter server value; treat that as an
accident guard, not the boundary.

**Post-sync checks** (written to `ops`):
- Over-count: a breakdown grain may not exceed `core` by more than 1%.
- Freshness per source: last successful sync within its budget. A dead token or a silent empty pull fails here.
- Row-count sanity against the previous window.

**Secrets:** `.dlt/secrets.toml` and `.env` are never committed; `*.example` files document every key.

---

## 8. Repository layout

```
omw/                    Python package (uv, Python 3.12)
  sources/              one module per source: meta_ads, google_ads, ga4, search_console, linkedin_ads, twenty, stripe
  pacing/               Meta header-aware pacer
  pipelines/            run entry points + cadences
  checks/               post-sync checks
  metrics/              metric-definition schema, compiler, MCP server
metrics/                metric definitions (YAML)
sql/                    ClickHouse DDL: databases, marts views, users, profiles, quotas
deploy/                 docker-compose.yml (+ profiles chat, observability), ClickHouse config
tests/                  unit tests with recorded API fixtures; integration tests against ClickHouse in Docker
SEMANTICS.md            business semantics for the agent
```

Tests never call a live platform API. Record fixtures as JSON under `tests/fixtures/<source>/`, with
identifiers and amounts synthetic.

---

## 9. Build threads

Each thread is one branch and one pull request. Briefs in `docs/THREADS.md`.

| # | Thread | Depends on |
|---|---|---|
| T1 | Skeleton: package, compose, ClickHouse users/profiles/quotas, CI | — |
| T2 | Search Console source + chunked backfill | T1 layout |
| T3 | Meta pacing layer + cadences | T1 layout |
| T4 | Google Ads GAQL reports | T1 layout |
| T5 | GA4 grains + Stripe | T1 layout |
| T6 | Metric layer: `SEMANTICS.md`, YAML schema, `query_metrics` server | T1 layout |
| T7 | Post-sync checks + freshness | T1 layout |
| T8 | LinkedIn Ads source | T1 layout |
| T9 | Twenty source | T1 layout |

Threads follow the layout in §8 from the start, so they can run in parallel; T1 owns `pyproject.toml`,
compose and CI, and the others add only their own dependencies.

---

## 10. Open questions (configuration, not blockers)

- Where it runs (local host or VPS). Webhooks need the VPS; the warehouse can live either side.
- Meta insights level per account (§5).
- LinkedIn advertising API access: approval terms.
- Agent caps: final row and time limits.

## Credits

Patterns re-authored from: dlt verified sources (Apache-2.0); ClickHouse `mcp-clickhouse`,
`agentic-data-stack` and `agent-skills` (Apache-2.0); Bhargava-R-dev `marketing-data-hub` (MIT: report grains,
over-count check, rolling replace, chunked backfill, `query_metrics` shape); Kaelio `ktx` (Apache-2.0: fan- and
chasm-trap handling, read-only SQL guard); datumwise `columna` (Apache-2.0: answer moods); cesteral
`mcp-open-advertising` (Apache-2.0: Meta usage-header parsing, LinkedIn version pinning). See `NOTICE`.
