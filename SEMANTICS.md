# Semantics for the agent

What each number means, where it lives and what it hides. Metrics are defined in `metrics/*.yaml` and served
by the `query_metrics` tool, which answers with a mood: **serve**, **disclose** (read the caveats),
**clarify** (pick a candidate) or **refuse** (no valid path). Every table is one *report grain*; filter on
exactly one `report`, never sum two grains together. Rates and cross-source measures are computed at query
time from their parts; never sum a rate yourself.

## Ad spend and delivery

- **meta_spend** (currency): Meta Ads amount spent, per account, campaign and day, in the account currency.
  Meta restates spend and conversions for up to **28 days**; recent days change. `meta_clicks` counts every
  click on the ad, not only link clicks. `meta_reach` is people reached and is **not additive**: ask for it
  at its own grain only. `meta_impressions` counts ad views; `meta_cpc` and `meta_ctr` are recomputed from
  spend, clicks and impressions.
- **google_spend** (currency): Google Ads cost per customer account, campaign and day (`cost_micros` /
  1 000 000). Conversions are attributed for up to **30 days** after the click, so recent conversions grow.
  `google_conversions` are the account's own conversion actions, fractional with modelled attribution.
  `google_impressions`, `google_clicks` and `google_cpc` follow the same rules; `google_ad_group_spend` is
  the ad group grain and sums to the campaign grain.
- **linkedin_spend** (currency): LinkedIn Ads cost per sponsored account, campaign and day. Cost and
  conversions settle over a few days (the last **7 days** are re-pulled). `linkedin_clicks` are chargeable
  clicks; `linkedin_conversions` are Insight Tag conversions, post-click and post-view;
  `linkedin_impressions` and `linkedin_cpc` follow Meta's rules.
- **ad_spend** = meta_spend + google_spend + linkedin_spend and **blended_cpc** = ad_spend / all clicks,
  only at the common grain (date, campaign_key, currency). Never sum across currencies: add the `currency`
  dimension or filter to one.
- `spend`, `cost`, `clicks`, `impressions`, `conversions`, `ctr`, `cpc` are ambiguous across platforms: the
  tool asks you to clarify. Name the platform (`meta_spend`, `google_spend`, `linkedin_spend`) or the
  blended measure (`ad_spend`).

## Organic search (Search Console)

- **search_clicks**, **search_impressions**, **search_ctr**, **search_position** come from the `core` grain
  per property and search type. Position is impression-weighted; 1 is the top.
- **query_clicks** (per query) and **page_clicks** (per page) are breakdown grains that **undercount core**:
  Google withholds anonymised queries. Days up to 3 days back are provisional.

## Site analytics (GA4)

- **sessions**, **new_users**, **key_events** per property and day. `key_events` are what the property
  configured as key events (formerly conversions). **users** is distinct users per day and is **not
  additive** across days.
- **channel_sessions** and **campaign_sessions** are breakdown grains. GA4 folds high-cardinality values into
  an **(other)** row, kept and flagged `is_other`, so the breakdown still sums to `core`. `(not set)` means
  no campaign data. GA4 `total_revenue` is modelled; billing truth is Stripe.

## Revenue (Stripe)

- **revenue** (currency): paid invoice amounts in the invoice currency (ISO 4217 upper case, like the ad
  platforms), dated by the day the invoice was paid. Refunds and voids are not netted. **paid_invoices** and
  **new_customers** (first subscription invoices) are counts; **paying_customers** is a distinct count, not
  additive across days.
- **campaign_key** on revenue comes from the invoice, else the subscription, else the customer metadata
  (`campaign_key` or `utm_campaign`). Revenue without a key is real revenue: `attributed = false`, never
  dropped when comparing to spend.

## Sales pipeline (Twenty CRM)

- **opportunities** and **pipeline_amount** (currency) count opportunities and sum their amounts by the day
  each was **created**, at its current stage and amount, so past days change as deals move.
  **won_opportunities** and **won_amount** are the ones now in the `CUSTOMER` stage (the default closed-won
  stage; stage names are the Twenty configuration). `campaign_key` resolves opportunity, then contact, then
  company; unattributed pipeline is real pipeline.

## Cross-source measures

- **roas** = revenue / ad_spend, **cac** = ad_spend / new_customers and **pipeline_cac** = ad_spend /
  opportunities. Each side is aggregated to the common grain (date, campaign_key, currency) first, then
  joined; a dimension or filter that exists on only one side is refused. The campaign key joins platforms
  through `marts.campaign_keys`: by default the slug of the campaign name (`widgets_prospecting`), so Stripe
  and Twenty attribution must carry that slug (or a manual mapping) for ROAS and CAC by campaign to join.

## Time and trust

- `date` is the day in each source's own time zone (ad account, property, UTC for Stripe and Twenty).
  `time_range` is `{start, end}` or a name: `today`, `yesterday`, `last_7_days`, `last_30_days`,
  `last_90_days` (ending yesterday), `month_to_date`, `last_month`, `year_to_date`. `compare` adds a
  `period` column (`current` / `previous`) for the previous period, week, month or year.
- The post-sync checks travel with the numbers: a source past its freshness budget, or a failed over-count
  or row-count check on the report used, is disclosed as a caveat. Verdicts live in `ops.freshness` and
  `ops.check_verdicts`.
