# Evaluation of the agent read path

The same 129 marketing questions are asked two ways, against the same seeded warehouse, and scored against
ground truth computed outside both:

- **Arm `sql`**: the agent gets the `marts` schema with its column comments, and raw SQL through
  mcp-clickhouse as the `agent` user.
- **Arm `metrics`**: the agent gets `SEMANTICS.md` and the `query_metrics` tools.

**What the offline run shows.** With the best plan, raw SQL can answer all 101 answerable questions and the
metric layer 92; it has no measure for the other 9. Taken at face value, the same questions give a wrong
number 30% of the time through raw SQL and 7% of the time through the metric layer, which also turns 20 of
28 trap questions into a refusal or a question back where raw SQL does so for 5. The eval also found five
gaps in the metric layer itself, listed under [What the eval found](#what-the-eval-found-in-the-metric-layer).

**What it does not show.** No model was run for these numbers. The offline planners follow plans written by
hand, so the results describe the two *tool surfaces*, not how well a model uses them. A real-model run is
one command away and is recorded separately ([below](#a-real-model-run)); none is recorded yet.

## Results

`reference` and `literal` are the two scripted planners, `refuser` is a null baseline; each is explained
under [Planners](#planners). Intervals are 95% Wilson intervals.

<!-- results:begin (written by `omw eval report`; do not edit by hand) -->

| Planner | Arm | Accuracy on answerable questions | Refuse/clarify correct on traps | Wrong-number rate (all questions) | Over-refusals | Caveats carried | Errors |
|---|---|---|---|---|---|---|---|
| `reference` (known-good plans: the harness check) | schema + raw SQL | 100.0% (101/101; 95% CI 96-100%) | 100.0% (28/28; 95% CI 88-100%) | 0.0% (0/129; 95% CI 0-3%) | 0/101 | 5/5 | 0 |
| `reference` (known-good plans: the harness check) | SEMANTICS.md + query_metrics | 91.1% (92/101; 95% CI 84-95%) | 100.0% (28/28; 95% CI 88-100%) | 0.0% (0/129; 95% CI 0-3%) | 9/101 | 5/5 | 0 |
| `literal` (face-value plans) | schema + raw SQL | 84.2% (85/101; 95% CI 76-90%) | 17.9% (5/28; 95% CI 8-36%) | 30.2% (39/129; 95% CI 23-39%) | 0/101 | 0/5 | 0 |
| `literal` (face-value plans) | SEMANTICS.md + query_metrics | 89.1% (90/101; 95% CI 82-94%) | 71.4% (20/28; 95% CI 53-85%) | 7.0% (9/129; 95% CI 4-13%) | 10/101 | 5/5 | 0 |
| `refuser` (declines everything: the null baseline) | schema + raw SQL | 0.0% (0/101; 95% CI 0-4%) | 60.7% (17/28; 95% CI 42-76%) | 0.0% (0/129; 95% CI 0-3%) | 101/101 | - | 0 |
| `refuser` (declines everything: the null baseline) | SEMANTICS.md + query_metrics | 0.0% (0/101; 95% CI 0-4%) | 60.7% (17/28; 95% CI 42-76%) | 0.0% (0/129; 95% CI 0-3%) | 101/101 | - | 0 |

### Face-value plans, by category

| Category | Questions | Correct: schema + raw SQL | Correct: SEMANTICS.md + query_metrics | Wrong numbers: schema + raw SQL | Wrong numbers: SEMANTICS.md + query_metrics |
|---|---|---|---|---|---|
| spend | 24 | 24/24 | 22/24 | 0 | 0 |
| rates | 13 | 8/13 | 13/13 | 5 | 0 |
| search | 10 | 9/10 | 7/10 | 1 | 0 |
| ga4 | 9 | 8/9 | 8/9 | 1 | 0 |
| revenue | 12 | 9/12 | 10/12 | 3 | 0 |
| pipeline | 10 | 9/10 | 9/10 | 1 | 0 |
| cross source | 13 | 9/13 | 12/13 | 4 | 0 |
| compare | 10 | 9/10 | 9/10 | 1 | 1 |
| trap | 28 | 5/28 | 20/28 | 23 | 8 |

### Face-value plans on traps, by kind of trap

| Kind of trap | Traps | Handled: schema + raw SQL | Handled: SEMANTICS.md + query_metrics |
|---|---|---|---|
| ambiguous name | 6 | 0/6 | 6/6 |
| mixed definitions | 2 | 0/2 | 0/2 |
| currency | 4 | 0/4 | 0/4 |
| vague | 2 | 0/2 | 0/2 |
| non additive | 3 | 0/3 | 3/3 |
| one sided | 5 | 0/5 | 5/5 |
| not in warehouse | 3 | 2/3 | 3/3 |
| boundary | 3 | 3/3 | 3/3 |

### Face-value requests the metric layer did not turn into a right answer

Beyond the coverage gaps in the next table.

| Question | Outcome | What happened |
|---|---|---|
| `ad_spend_widgets_prospecting_usd_sep` | over refusal | refuse: the warehouse refused the query: Received ClickHouse exception, code: 47, server response: Code: 47. DB::Exception: Unknown expression or function identifier `... |
| `meta_spend_sep_vs_aug` | wrong number | 'previous': got 24619.39, want 25455.74 |
| `total_conversions_all_sources_sep` | wrong number | answered a question that has no single right number |
| `total_ad_spend_sep` | wrong number | answered a question that has no single right number |
| `total_revenue_last_month` | wrong number | answered a question that has no single right number |
| `roas_last_month` | wrong number | answered a question that has no single right number |
| `cac_no_period` | wrong number | answered a question that has no single right number |
| `gadgets_launch_performance` | wrong number | answered a question that has no single right number |
| `sum_two_search_grains` | wrong number | answered a question that has no single right number |
| `forecast_next_month` | wrong number | answered a question that has no single right number |

### Questions the metric layer could not answer with the best plan

| Question | Outcome | What happened |
|---|---|---|
| `meta_spend_by_ad_gadgets_sep` | over refusal | refuse: no metric in the catalog expresses this |
| `meta_daily_budgets` | over refusal | refuse: no metric in the catalog expresses this |
| `search_clicks_by_device_sep` | over refusal | refuse: no metric in the catalog expresses this |
| `top_search_country_aug` | over refusal | refuse: no metric in the catalog expresses this |
| `top_page_for_query_sep` | over refusal | refuse: no metric in the catalog expresses this |
| `top_landing_pages_sep` | over refusal | refuse: no metric in the catalog expresses this |
| `paying_customers_sep` | over refusal | refuse: paying_customers is not additive: it can only be asked at its own grain ['attributed', 'billing_reason', 'campaign_key', 'currency', 'date', 'utm_campaign', 'u... |
| `active_subscriptions` | over refusal | refuse: no metric in the catalog expresses this |
| `crm_tasks_sep` | over refusal | refuse: crm_tasks is not additive: it can only be asked at its own grain ['campaign_key', 'company_id', 'company_name', 'created_by', 'date', 'kind', 'opportunity_id',... |

<!-- results:end -->

## Reading the results

- **`reference`, both arms: the harness check.** Every reference query and every reference `query_metrics`
  request returns the independently computed ground truth, through the repo's real `marts` views. That is
  three separate computations agreeing on 101 numbers. The 28/28 on traps is scripted (the reference planner
  declines them by definition), so read it as "the scorer accepts the right behaviour", nothing more.
- **`reference`, metric arm: coverage.** The 9 over-refusals are questions the layer has no way to answer:
  7 need a grain with no metric (Meta ads, budgets, Search Console devices, countries and query-by-page,
  GA4 landing pages, the subscription list) and 2 are distinct counts the layer refuses above their own
  grain. It never returned a wrong number.
- **`literal`, SQL arm: nothing stops a face-value query.** 19 answerable questions carry the tempting
  query (average the rate column, read the breakdown grain, join two fact tables row by row). 16 of those
  return a wrong number; 3 land within the tolerance on this data, because daily volumes inside one campaign
  are steady. The trap results in this arm are by construction: 23 traps carry a face-value query and each
  one returns a number. The 5 handled are the 2 with no table to query and the 3 the warehouse itself
  rejects (`raw` is invisible to the agent, and a write is refused).
- **`literal`, metric arm: what the layer does with the same face-value request.** This is the measured
  part. It recomputes every rate, refuses the non-additive measures and the one-sided cross-source
  breakdowns, and asks back on the six ambiguous names. It does not catch a mixed-currency total, cannot
  catch arithmetic the agent does after the tool call, and cannot see that a question is vague.
- **Caveats only travel with the metric layer.** Five questions need a caveat beside a right number (Meta
  restatement, Google conversion lag, withheld search terms, anonymised queries, a stale source). The layer
  attached it every time; a SQL result carries none unless the agent writes one.
- **`refuser`: why trap scores need accuracy beside them.** Declining everything gives no wrong number,
  no answer, and 17 of 28 traps, because the other 11 call for a question back, not a refusal.

## What the eval found in the metric layer

These are findings about `omw/metrics`, which this thread does not own. They are left as they are and
reported, so the numbers above are the layer's real behaviour.

1. **A filter on `campaign_key` without the dimension fails.** `query_metrics(["ad_spend"], filters={
   "campaign_key": "widgets_prospecting"})` compiles SQL that filters on a column the ad platform views do
   not have, and answers `refuse` with a ClickHouse error. Asking for the dimension and picking the row
   works (`ad_spend_widgets_prospecting_usd_sep`).
2. **A mixed-currency total is served with a caveat.** `ad_spend`, `revenue`, `roas` and `cac` without a
   currency return one number that adds dollars to euros, in mood `disclose`. `SEMANTICS.md` says never to
   sum across currencies; the four `currency` traps are the layer's only wrong numbers on traps that it
   could have stopped.
3. **Distinct counts are refused above their own grain.** `paying_customers` and `crm_tasks` are
   `uniqExact` over row-level views, which is exact at any grain, yet "paying customers in September" is
   refused as not additive. The rule fits `meta_reach` and GA4 `users`, which arrive pre-aggregated per day.
4. **`compare: "month"` is not the calendar month before.** It shifts both dates back a month, so September
   compares with 1 to 30 August and drops the 31st (`meta_spend_sep_vs_aug`). The answer does not state the
   window it compared with; only the `sql` field does.
5. **Coverage.** No metric reads `marts.meta_ads_ads`, the campaign and budget lists,
   `search_console_devices`, `search_console_countries`, `search_console_query_page`, `ga4_landing_pages`
   or `stripe_subscriptions`.

## How it works

### The seed

`omw eval seed` loads a synthetic company into ClickHouse: four months (1 June to 30 September 2026), every
source and every report grain, about 33 000 rows in 49 `raw` tables. The evaluation pretends today is
**1 October 2026**, so "last month" and Meta's 28-day restatement window mean the same thing on every run.

- **Deterministic.** Every number is a hash of the seed (`20260601`), an entity, a day and a field name
  (`omw/eval/generate/rng.py`), not a draw from a stream. The same seed gives the same warehouse on any
  machine, and adding a campaign does not move the numbers of the others.
- **Only `raw` is written.** The seed creates the tables a dlt run leaves behind (column for column, from
  the fixture loads in `tests/integration`) and inserts rows. Everything the agent reads then comes from the
  repo's own code: the `marts` views in `sql/`, the campaign keys from `omw.metrics.campaign_keys`, the
  check verdicts from `omw.checks`. All 28 over-count and row-count checks pass on the synthetic data.
- **Shaped like the real traps.** Breakdown grains sum to their parent to the unit. Search Console queries
  and Google search terms undercount it, as the real ones do. Two currencies (a euro Google Ads account,
  euro invoices). Campaigns that share a key across platforms through the slug of their name, plus one
  mapped by hand. Stripe attribution on the invoice, the subscription or the customer, sometimes
  conflicting. Unpaid and void invoices, deleted CRM records, notes attached to two records. GA4 is left
  80 hours stale, so its answers must say so.
- **Guarded.** Seeding replaces the raw tables and marts views of all seven sources. It refuses to run on a
  warehouse that holds source data it did not seed, unless told `--force`. `omw eval drop` removes it all.

| Source | Report grains seeded |
|---|---|
| Meta Ads | campaigns, age and gender, countries, placements, ads, leads, campaign and ad set lists |
| Google Ads | campaigns, ad groups, search terms, asset groups, campaign list |
| LinkedIn Ads | campaigns, creatives, leads, campaign list |
| Search Console | core, queries, pages, query by page, countries, devices |
| GA4 | core, channels, campaigns, landing pages |
| Stripe | invoices (revenue), balance transactions, subscriptions |
| Twenty | opportunities (pipeline), activity (notes and tasks) |

### Ground truth

A question never stores its answer. It carries a small Python function over the generated rows
(`omw/eval/truth.py`), evaluated without ClickHouse. The generator produces two views of the data on
purpose: the rows as the agent should see them, which the truth is computed from, and the rows as dlt
loads them, which is what gets inserted. Where a view does real work (Stripe and Twenty attribution, paid
invoices only, deleted records), the generator resolves it independently, so a view that disagrees with the
truth shows up as a failing test, not as a ground truth that agrees with itself.

`eval/questions.json` publishes every question with its computed answer. A unit test fails when that file
no longer matches the seed.

### Questions

| Category | Count | What it covers |
|---|---|---|
| spend | 24 | Spend, impressions, clicks and conversions on one platform, across every grain |
| rates | 13 | CTR, CPC, CPM, conversion rate, position: recomputed, never averaged |
| search | 10 | Search Console totals, top queries and pages, breakdowns |
| ga4 | 9 | Sessions, users, key events, channels, campaigns, the `(other)` bucket |
| revenue | 12 | Stripe revenue, new customers, attribution, net revenue, refunds, fees |
| pipeline | 10 | Twenty opportunities, amounts, stages, win rate, CRM tasks |
| cross source | 13 | Ad spend across platforms, ROAS, CAC, cost per opportunity, cost per lead |
| compare | 10 | A period against the one before, month over month, percentage change |
| trap | 28 | 11 that need a question back, 17 that have no valid answer |

Each question states its period and its unit, and ends with an answer format ("a single number (USD)",
"one row per campaign name"). Traps get the same kind of format line, so the format never gives away that a
question is a trap. 22 answerable questions are tagged `pitfall`: there is a tempting wrong way to answer
them. The traps come in eight kinds:

| Kind | Example | Right behaviour |
|---|---|---|
| ambiguous name | "How many clicks did we get last month?" | clarify: which platform |
| currency | "What was our total ad spend in September 2026?" | clarify: dollars and euros |
| vague | "How did the Gadgets Launch campaign do last month?" | clarify: which measure |
| mixed definitions | "Total conversions across Google Ads, LinkedIn and GA4" | refuse or clarify |
| non additive | "How many people did our Meta Ads reach in September 2026?" | refuse |
| one sided | "What was our ROAS by Meta age bracket?" | refuse: revenue has no age |
| not in warehouse | "How much did we spend on TikTok Ads?" | refuse |
| boundary | "How many Stripe invoices are still unpaid?" | refuse: only paid ones leave `raw` |

### Planners

The planner is the part of the agent that decides what to ask its tools and what to say back. It is a
small interface (`omw/eval/planner.py`): it is shown the question's words and a toolbox, and returns a
structured answer. It never receives a reference plan or the ground truth.

- **`reference`** follows each question's reference plan: one SQL query, or one or two `query_metrics`
  requests. It is the careful analyst who knows the traps, and the harness's known-good run.
- **`literal`** takes each question at face value: the query or request a hurried analyst would send. For
  most questions that is the reference plan. For the pitfalls and the traps it is the tempting one.
- **`refuser`** declines everything. A null baseline.
- **`model`** puts a real model in the seat ([below](#a-real-model-run)).

### Scoring

Every answer is `answer` (a number, a name, or label and number rows), `clarify`, `refuse`, or `error`.
Numbers match within 0.5%. Rows match as a mapping: a row with no value or zero may be added or left out.
A ranked answer must also be in order.

| Outcome | Meaning |
|---|---|
| correct | The right number, or an accepted refusal or question back on a trap |
| wrong number | A number that is wrong, or any number at all on a trap |
| over-refusal | An answerable question declined |
| wrong mood | A trap declined the wrong way (refused where it should have asked) |
| no answer | An answer claimed with no value in it |
| error | No scorable output: the quota ran out, the stack was down, a model call failed |

- **Accuracy** is correct answers over answerable questions.
- **Refuse/clarify correctness** is correctly handled traps over traps.
- **Wrong-number rate** is wrong numbers over all questions.

`error` rows are counted and left out of all three, and `omw eval run` exits non-zero when there are any:
a dead warehouse must never read as a model that refused.

## Run it

```bash
make up
```

```bash
uv run omw eval seed
```

```bash
uv run omw eval report
```

`report` runs the three offline planners through both arms (about 15 seconds), writes
`eval/results/scripted.json` and `eval/questions.json`, and rewrites the results section above. CI runs the
same thing as tests: `tests/unit/eval` (no services) and `tests/integration/test_eval.py` (seeds the stack,
checks every `marts` view against the generated rows, runs both planners through both arms, and fails if
the published results no longer match a fresh run).

Other commands:

```bash
uv run omw eval run --planner literal --only trap --arms metrics
```

```bash
uv run omw eval drop
```

Drop the seed before running the rest of the integration suite on the same stack: other test modules
expect a warehouse that holds only what they load (`tests/integration/test_eval.py` seeds and drops by
itself).

The `agent` user is limited to 1000 queries an hour. A full report costs about 240, because the harness
runs each distinct query once. If repeated runs do hit the quota, the rows are recorded as errors;
`make bootstrap` resets the counter on a dev stack.

## A real-model run

Optional, and it costs money: 258 conversations (129 questions in two arms), a few model calls each. The
SQL arm's schema is about 46 000 characters, sent as a cached system prompt.

```bash
uv sync --group eval
```

```bash
uv run omw eval run --planner model --model claude-opus-5-5 --effort medium
```

The Anthropic SDK reads its credentials from the environment (`ANTHROPIC_API_KEY`, or an `ant auth login`
profile). The model gets the same instructions in both arms, the arm's document and tools, and one more
tool, `submit_answer`, that ends the conversation with a structured answer.

The run is written to `eval/results/model-<model>.json` with every answer, every tool call, the serving
model and the token counts per question. `omw eval report` then adds it to the results table. Try a slice
first:

```bash
uv run omw eval run --planner model --only rates --arms metrics --out rates-slice.json
```

Three things are deliberate:

- **No fallback model.** A response served by another model, a refusal by the safety classifiers or a
  truncated reply is an `error` row. A score from the wrong model measures nothing.
- **Repeats.** A model is not deterministic. `--reps 3` asks every question three times. One pass over 101
  answerable questions has a 95% interval of roughly ±8 points, and 28 traps roughly ±17, so a smaller
  difference between two runs or two arms is noise.
- **Another model.** `omw/eval/model_planner.py` is under 200 lines over one interface. A planner for
  another provider is a class with `name` and `solve(task, toolbox)`.

## Limits

- **The scripted results are not model results.** The face-value plans were written by hand for this eval.
  They encode the traps `SEMANTICS.md` documents, which is fair to the layer and may flatter it.
- **Synthetic data.** Volumes are steady and clean. Three of the nineteen face-value queries are right here
  only because of that; on real data with uneven days, more would be wrong.
- **One seed, one company, one date.** The ground truth is published for seed `20260601` only.
- **Single-turn.** A question is asked once and answered once. A real agent can ask back and continue; a
  `clarify` here ends the conversation and is scored as the right or wrong move.
- **The metric arm has no SQL.** It measures the layer on its own. An agent with both tools would answer
  the coverage gaps through SQL, and would be exposed to the SQL arm's traps while doing it.
