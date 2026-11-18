# Evaluation of the agent read path

The same 134 marketing questions are asked two ways, against the same seeded warehouse, and scored against
ground truth computed outside both:

- **Arm `sql`**: the agent gets the `marts` schema with its column comments, and raw SQL through
  mcp-clickhouse as the `agent` user.
- **Arm `metrics`**: the agent gets `SEMANTICS.md` and the `query_metrics` tools.

**What the offline run shows.** With the best plan, raw SQL can answer all 106 answerable questions and the
metric layer 105; it has no measure for the other one. Taken at face value, the same questions give a wrong
number 29% of the time through raw SQL and 2% of the time through the metric layer, which also turns 25 of
28 trap questions into a refusal or a question back where raw SQL does so for 5. The first run of this eval
found five gaps in the metric layer itself (it answered 92 of its 101 answerable questions and gave 9 wrong
numbers at face value); they are fixed, and listed with what changed under
[What the eval found](#what-the-eval-found-in-the-metric-layer).

**What it does not show.** No model was run for these numbers. The offline planners follow plans written by
hand, so the results describe the two *tool surfaces*, not how well a model uses them. A real-model run is
one command away and is recorded separately ([below](#a-real-model-run)); none is recorded yet.

## Results

`reference` and `literal` are the two scripted planners, `refuser` is a null baseline; each is explained
under [Planners](#planners). Intervals are 95% Wilson intervals.

<!-- results:begin (written by `omw eval report`; do not edit by hand) -->

| Planner | Arm | Accuracy on answerable questions | Refuse/clarify correct on traps | Wrong-number rate (all questions) | Over-refusals | Caveats carried | Errors |
|---|---|---|---|---|---|---|---|
| `reference` (known-good plans: the harness check) | schema + raw SQL | 100.0% (106/106; 95% CI 97-100%) | 100.0% (28/28; 95% CI 88-100%) | 0.0% (0/134; 95% CI 0-3%) | 0/106 | 5/5 | 0 |
| `reference` (known-good plans: the harness check) | SEMANTICS.md + query_metrics | 99.1% (105/106; 95% CI 95-100%) | 100.0% (28/28; 95% CI 88-100%) | 0.0% (0/134; 95% CI 0-3%) | 1/106 | 5/5 | 0 |
| `literal` (face-value plans) | schema + raw SQL | 84.9% (90/106; 95% CI 77-90%) | 17.9% (5/28; 95% CI 8-36%) | 29.1% (39/134; 95% CI 22-37%) | 0/106 | 0/5 | 0 |
| `literal` (face-value plans) | SEMANTICS.md + query_metrics | 99.1% (105/106; 95% CI 95-100%) | 89.3% (25/28; 95% CI 73-96%) | 2.2% (3/134; 95% CI 1-6%) | 1/106 | 5/5 | 0 |
| `refuser` (declines everything: the null baseline) | schema + raw SQL | 0.0% (0/106; 95% CI 0-3%) | 60.7% (17/28; 95% CI 42-76%) | 0.0% (0/134; 95% CI 0-3%) | 106/106 | - | 0 |
| `refuser` (declines everything: the null baseline) | SEMANTICS.md + query_metrics | 0.0% (0/106; 95% CI 0-3%) | 60.7% (17/28; 95% CI 42-76%) | 0.0% (0/134; 95% CI 0-3%) | 106/106 | - | 0 |

### Face-value plans, by category

| Category | Questions | Correct: schema + raw SQL | Correct: SEMANTICS.md + query_metrics | Wrong numbers: schema + raw SQL | Wrong numbers: SEMANTICS.md + query_metrics |
|---|---|---|---|---|---|
| spend | 24 | 24/24 | 24/24 | 0 | 0 |
| rates | 13 | 8/13 | 13/13 | 5 | 0 |
| search | 10 | 9/10 | 10/10 | 1 | 0 |
| ga4 | 11 | 10/11 | 11/11 | 1 | 0 |
| revenue | 13 | 10/13 | 12/13 | 3 | 0 |
| pipeline | 12 | 11/12 | 12/12 | 1 | 0 |
| cross source | 13 | 9/13 | 13/13 | 4 | 0 |
| compare | 10 | 9/10 | 10/10 | 1 | 0 |
| trap | 28 | 5/28 | 25/28 | 23 | 3 |

### Face-value plans on traps, by kind of trap

| Kind of trap | Traps | Handled: schema + raw SQL | Handled: SEMANTICS.md + query_metrics |
|---|---|---|---|
| ambiguous name | 6 | 0/6 | 6/6 |
| mixed definitions | 2 | 0/2 | 0/2 |
| currency | 4 | 0/4 | 4/4 |
| vague | 2 | 0/2 | 1/2 |
| non additive | 3 | 0/3 | 3/3 |
| one sided | 5 | 0/5 | 5/5 |
| not in warehouse | 3 | 2/3 | 3/3 |
| boundary | 3 | 3/3 | 3/3 |

### Face-value requests the metric layer did not turn into a right answer

Beyond the coverage gaps in the next table.

| Question | Outcome | What happened |
|---|---|---|
| `total_conversions_all_sources_sep` | wrong number | answered a question that has no single right number |
| `sum_two_search_grains` | wrong number | answered a question that has no single right number |
| `forecast_next_month` | wrong number | answered a question that has no single right number |

### Questions the metric layer could not answer with the best plan

| Question | Outcome | What happened |
|---|---|---|
| `active_subscription_prices_usd` | over refusal | refuse: no metric in the catalog expresses this |

<!-- results:end -->

## Reading the results

- **`reference`, both arms: the harness check.** Every reference query and every reference `query_metrics`
  request returns the independently computed ground truth, through the repo's real `marts` views. That is
  three separate computations agreeing on 106 numbers. The 28/28 on traps is scripted (the reference planner
  declines them by definition), so read it as "the scorer accepts the right behaviour", nothing more.
- **`reference`, metric arm: coverage.** The one over-refusal is a question the layer has no way to
  answer: the unit prices of the active subscriptions (no metric reads subscription prices). It is declared
  as a gap (`not_in_catalog`), declined, and never answered with a wrong number. In the first run 9 questions
  were in that state: 7 needed a grain with no metric and 2 were distinct counts refused above their own
  grain (see below). One gap the eval cannot probe: the catalog has no measure for Meta conversions (the
  `actions` JSON), and the seed leaves that column empty.
- **`literal`, SQL arm: nothing stops a face-value query.** 19 answerable questions carry the tempting
  query (average the rate column, read the breakdown grain, join two fact tables row by row). 16 of those
  return a wrong number; 3 land within the tolerance on this data, because daily volumes inside one campaign
  are steady. The trap results in this arm are by construction: 23 traps carry a face-value query and each
  one returns a number. The 5 handled are the 2 with no table to query and the 3 the warehouse itself
  rejects (`raw` is invisible to the agent, and a write is refused).
- **`literal`, metric arm: what the layer does with the same face-value request.** This is the measured
  part. It recomputes every rate and every distinct count, refuses the non-additive measures and the
  one-sided cross-source breakdowns, asks back on the six ambiguous names and asks which currency on the
  four totals that would add dollars to euros. The three wrong numbers left are requests the layer answers
  correctly as asked: two where the agent adds columns up itself after the call (conversions from three
  sources, clicks from two grains) and a forecast answered with last month's spend. It cannot catch
  arithmetic done after the tool call and cannot see that a question is vague. The one `vague` trap it does
  stop (`gadgets_launch_performance`) it stops for another reason: the face-value request asks for Meta
  spend without a currency, so the question back is about the currency, not about which measure was meant.
- **Caveats only travel with the metric layer.** Five questions need a caveat beside a right number (Meta
  restatement, Google conversion lag, withheld search terms, anonymised queries, a stale source). The layer
  attached it every time; a SQL result carries none unless the agent writes one.
- **`refuser`: why trap scores need accuracy beside them.** Declining everything gives no wrong number,
  no answer, and 17 of 28 traps, because the other 11 call for a question back, not a refusal.

## What the eval found in the metric layer

The first run of this eval (thread T10) found five gaps in `omw/metrics`. It reported them and left the
layer as it was, so its numbers were the layer's real behaviour: 92 of 101 answerable questions with the
best plan, 9 wrong numbers and 20 of 28 traps handled at face value. Thread T15 fixed all five. The
questions and their ground truth are the same; only the plans that worked around a gap, or declared one,
changed. The table above is the layer after the fixes.

| | First run | Now |
|---|---|---|
| Answerable questions the best plan answers | 92/101 | 101/101 |
| Face-value requests answered right | 90/101 | 101/101 |
| Traps handled at face value | 20/28 | 25/28 |
| Wrong numbers at face value | 9/129 | 3/129 |

Both columns count the 129 questions of the first run. Five were added afterwards, so the tables at the top
count 134: two GA4 totals the catalog gained a metric for (`page_views`, `engaged_sessions`), one
question it still cannot answer, kept as a declared gap so that coverage stays a measured number, and two
on Twenty's change feed (deals won in a month by the day they moved, stage moves by stage), added with the
`timeline` grain.

1. **A filter on `campaign_key` without the dimension failed.** `query_metrics(["ad_spend"], filters={
   "campaign_key": "widgets_prospecting"})` compiled SQL that filtered on a column the ad platform views do
   not have, and answered `refuse` with a ClickHouse error. *Fixed:* the filter reads the same expression
   the dimension does, joined through `marts.campaign_keys`. A CI check (`omw/metrics/ddl.py`) now compares
   every dimension and metric column in the catalog with the views in `sql/`, so a column a view lacks
   fails a test, not a query (`ad_spend_widgets_prospecting_usd_sep`, face-value plan).
2. **A mixed-currency total was served with a caveat.** `ad_spend`, `revenue`, `roas` and `cac` without a
   currency returned one number that added dollars to euros, in mood `disclose`. *Fixed:* a measure in a
   currency, or built from one, asked without the `currency` dimension or a filter on one currency answers
   `clarify` with the two ways to choose. The rule follows the metric's unit, so budgets, CPC and every
   future money measure are covered; refusals are checked first, so a request with no valid path is still
   refused (the four `currency` traps).
3. **Distinct counts were refused above their own grain.** `paying_customers` and `crm_tasks` are
   `uniqExact` over row-level views, exact at any grain, yet "paying customers in September" was refused.
   *Fixed:* the YAML declares it. A report is `row_level` or not, a metric is `aggregation: distinct`
   (recounted at the requested grain) or `native` (Meta reach, GA4 users: deduplicated per day by the
   source, still refused above their grain). The schema rejects a distinct count on a report that is not
   row-level, and the CI check rejects a `row_level` report whose view groups and a `sum` over a column the
   view's comment calls not additive (`paying_customers_sep`, `crm_tasks_sep`).
4. **`compare: "month"` was not the calendar month before.** It shifted both dates back a month, so
   September compared with 1 to 30 August. *Fixed:* whole calendar months compare with whole calendar
   months (`year` likewise, leap days included), and the answer states both windows in a structured
   `comparison` field. `week` on a range longer than seven days used to drop the shared days from the
   previous total; both periods now hold all of their days and the overlap is disclosed. The reference
   plans for the month-over-month questions now use `compare: "month"` (`meta_spend_sep_vs_aug`).
5. **Coverage.** No metric read `marts.meta_ads_ads`, the campaign and budget lists,
   `search_console_devices`, `search_console_countries`, `search_console_query_page`, `ga4_landing_pages`
   or `stripe_subscriptions`. *Fixed:* 20 metrics over 10 new report grains, each in `SEMANTICS.md`. The
   campaign, ad set and subscription lists are declared `snapshot`: the state at the last sync, with no
   date, no `time_range` and no `compare` (the seven questions that had no plan).

## How it works

### The seed

`omw eval seed` loads a synthetic company into ClickHouse: four months (1 June to 30 September 2026), every
source and every report grain, about 33 000 rows in 50 `raw` tables. The evaluation pretends today is
**1 October 2026**, so "last month" and Meta's 28-day restatement window mean the same thing on every run.

- **Deterministic.** Every number is a hash of the seed (`20260601`), an entity, a day and a field name
  (`omw/eval/generate/rng.py`), not a draw from a stream. The same seed gives the same warehouse on any
  machine in any time zone (timestamps are loaded as UTC instants), and adding a campaign does not move
  the numbers of the others.
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
| Twenty | opportunities (pipeline), activity (notes and tasks), timeline (the change feed: creations and stage moves) |

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
| ga4 | 11 | Sessions, page views, users, key events, channels, campaigns, the `(other)` bucket |
| revenue | 13 | Stripe revenue, new customers, attribution, net revenue, refunds, fees, subscriptions |
| pipeline | 12 | Twenty opportunities, amounts, stages, win rate, CRM tasks, wins and stage moves by the day they happened |
| cross source | 13 | Ad spend across platforms, ROAS, CAC, cost per opportunity, cost per lead |
| compare | 10 | A period against the one before, month over month, percentage change |
| trap | 28 | 11 that need a question back, 17 that have no valid answer |

Each question states its period and its unit, and ends with an answer format ("a single number (USD)",
"one row per campaign name"). Traps get the same kind of format line, so the format never gives away that a
question is a trap. 20 answerable questions are tagged `pitfall`: there is a tempting wrong way to answer
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

Optional, and it costs money: 268 conversations (134 questions in two arms), a few model calls each. The
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
- **Repeats.** A model is not deterministic. `--reps 3` asks every question three times. One pass over 106
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
  what the catalog does not cover through SQL, and would be exposed to the SQL arm's traps while doing it.
