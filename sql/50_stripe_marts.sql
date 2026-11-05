-- Stripe marts (DESIGN.md §4, T5): revenue by date and by campaign key where attribution metadata exists.
-- Amounts are Stripe minor units in raw. The views convert to currency amounts. Zero-decimal currencies
-- (JPY, KRW, ...) are already whole units in Stripe and are not divided. Applied by omw.pipelines.stripe after each load.

CREATE OR REPLACE VIEW marts.stripe_revenue (
    source LowCardinality(String) COMMENT 'Always stripe',
    report LowCardinality(String) COMMENT 'Report grain, always invoices: one row per paid invoice. Sum amount for revenue',
    date Date COMMENT 'Day the invoice was paid (UTC). Invoices can later be refunded or voided: refunds are not netted here',
    invoice_id String COMMENT 'Stripe invoice id',
    customer_id String COMMENT 'Stripe customer id',
    subscription_id String COMMENT 'Stripe subscription id, empty for one-off invoices',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency of the invoice, upper case (Stripe reports lower case)',
    amount Decimal(18, 2) COMMENT 'Amount paid in the invoice currency (minor units / 100, whole units for zero-decimal currencies)',
    amount_minor Int64 COMMENT 'Amount paid in Stripe minor units, as reported',
    billing_reason LowCardinality(String) COMMENT 'subscription_create, subscription_cycle, subscription_update, manual, ...',
    campaign_key String COMMENT 'Campaign key for cross-source joins (DESIGN.md §6): metadata.campaign_key on the invoice, else the subscription, else the customer. Empty when no attribution metadata exists',
    utm_source String COMMENT 'utm_source from metadata, invoice first then subscription then customer. Empty when absent',
    utm_medium String COMMENT 'utm_medium from metadata, same precedence',
    utm_campaign String COMMENT 'utm_campaign from metadata, same precedence',
    attributed Bool COMMENT 'True when any attribution metadata exists for the invoice. Unattributed revenue is real revenue: never drop it when comparing to spend'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT 'stripe' AS source, 'invoices' AS report, toDate(i.paid_at) AS date, i.id AS invoice_id,
       i.customer_id AS customer_id, i.subscription_id AS subscription_id, upper(i.currency) AS currency,
       if(i.currency IN ('bif','clp','djf','gnf','jpy','kmf','krw','mga','pyg','rwf','ugx','vnd','vuv','xaf','xof','xpf'),
          toDecimal64(i.amount_paid, 2), toDecimal64(i.amount_paid, 2) / 100) AS amount,
       i.amount_paid AS amount_minor, i.billing_reason AS billing_reason,
       multiIf(i.campaign_key != '', i.campaign_key, s.campaign_key != '', s.campaign_key, c.campaign_key) AS campaign_key,
       multiIf(i.utm_source != '', i.utm_source, s.utm_source != '', s.utm_source, c.utm_source) AS utm_source,
       multiIf(i.utm_medium != '', i.utm_medium, s.utm_medium != '', s.utm_medium, c.utm_medium) AS utm_medium,
       multiIf(i.utm_campaign != '', i.utm_campaign, s.utm_campaign != '', s.utm_campaign, c.utm_campaign) AS utm_campaign,
       (campaign_key != '' OR utm_source != '' OR utm_medium != '' OR utm_campaign != '') AS attributed
FROM raw.stripe___invoices AS i
LEFT JOIN raw.stripe___subscriptions AS s ON s.id = i.subscription_id
LEFT JOIN raw.stripe___customers AS c ON c.id = i.customer_id
WHERE i.status = 'paid' AND i.amount_paid > 0;

CREATE OR REPLACE VIEW marts.stripe_revenue_daily (
    source LowCardinality(String) COMMENT 'Always stripe',
    report LowCardinality(String) COMMENT 'Report grain, always revenue_daily: one row per day, currency and campaign key. Sums to stripe_revenue',
    date Date COMMENT 'Day the invoices were paid (UTC)',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency, upper case. Never sum across currencies',
    campaign_key String COMMENT 'Campaign key for cross-source joins, empty for unattributed revenue',
    utm_source String COMMENT 'utm_source of the attribution, empty when absent',
    utm_medium String COMMENT 'utm_medium of the attribution, empty when absent',
    utm_campaign String COMMENT 'utm_campaign of the attribution, empty when absent',
    revenue Decimal(18, 2) COMMENT 'Sum of paid invoice amounts in the currency',
    invoices UInt64 COMMENT 'Paid invoices',
    customers UInt64 COMMENT 'Distinct paying customers. Not additive across days'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT source, 'revenue_daily' AS report, date, currency, campaign_key, utm_source, utm_medium, utm_campaign,
       sum(amount) AS revenue, count() AS invoices, uniqExact(customer_id) AS customers
FROM marts.stripe_revenue
GROUP BY source, date, currency, campaign_key, utm_source, utm_medium, utm_campaign;

CREATE OR REPLACE VIEW marts.stripe_subscriptions (
    source LowCardinality(String) COMMENT 'Always stripe',
    subscription_id String COMMENT 'Stripe subscription id',
    customer_id String COMMENT 'Stripe customer id',
    status LowCardinality(String) COMMENT 'active, trialing, past_due, canceled, unpaid, incomplete, incomplete_expired, paused',
    created DateTime COMMENT 'When the subscription was created (UTC)',
    canceled_at DateTime COMMENT 'When it was canceled (UTC), 1970-01-01 when not canceled',
    currency LowCardinality(String) COMMENT 'ISO 4217 currency, upper case',
    interval LowCardinality(String) COMMENT 'Billing interval of the first item: month, year, week, day',
    unit_amount Decimal(18, 2) COMMENT 'Price of the first item per interval in the currency',
    quantity UInt64 COMMENT 'Quantity of the first item',
    product_id String COMMENT 'Stripe product id of the first item',
    campaign_key String COMMENT 'Campaign key from the subscription metadata, else the customer metadata. Empty when absent',
    utm_source String COMMENT 'utm_source from the subscription metadata, else the customer. Empty when absent',
    utm_medium String COMMENT 'utm_medium, same precedence',
    utm_campaign String COMMENT 'utm_campaign, same precedence'
) DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT s.source AS source, s.id AS subscription_id, s.customer_id AS customer_id, s.status AS status, s.created AS created,
       s.canceled_at AS canceled_at, upper(s.currency) AS currency, s.interval AS interval,
       toDecimal64(s.unit_amount, 2) / 100 AS unit_amount, toUInt64(s.quantity) AS quantity, s.product_id AS product_id,
       if(s.campaign_key != '', s.campaign_key, c.campaign_key) AS campaign_key,
       if(s.utm_source != '', s.utm_source, c.utm_source) AS utm_source,
       if(s.utm_medium != '', s.utm_medium, c.utm_medium) AS utm_medium,
       if(s.utm_campaign != '', s.utm_campaign, c.utm_campaign) AS utm_campaign
FROM raw.stripe___subscriptions AS s
LEFT JOIN raw.stripe___customers AS c ON c.id = s.customer_id;
