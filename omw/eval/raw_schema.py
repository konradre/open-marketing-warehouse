"""The ``raw`` tables a dlt run leaves behind, column for column: taken from the tables the fixture loads
in ``tests/integration`` create, minus dlt's own ``_dlt_*`` bookkeeping columns. The seed creates exactly
these, so the ``marts`` views in ``sql/`` apply to it unchanged."""

from __future__ import annotations

import re

RAW_TABLES: dict[str, str] = {
    "ga4___campaigns": (
        "source String, report String, property_id String, date Date, campaign String, "
        "source_name String, medium String, is_other Bool, sessions Int64, total_users Int64, "
        "new_users Int64, engaged_sessions Int64, page_views Int64, key_events Float64, "
        "total_revenue Float64"
    ),
    "ga4___channels": (
        "source String, report String, property_id String, date Date, channel_group String, "
        "is_other Bool, sessions Int64, total_users Int64, new_users Int64, engaged_sessions Int64, "
        "page_views Int64, key_events Float64, total_revenue Float64"
    ),
    "ga4___core": (
        "source String, report String, property_id String, date Date, is_other Bool, sessions Int64, "
        "total_users Int64, new_users Int64, engaged_sessions Int64, page_views Int64, "
        "key_events Float64, total_revenue Float64"
    ),
    "ga4___landing_pages": (
        "source String, report String, property_id String, date Date, landing_page String, "
        "is_other Bool, sessions Int64, total_users Int64, new_users Int64, engaged_sessions Int64, "
        "page_views Int64, key_events Float64, total_revenue Float64"
    ),
    "ga4___sync_windows": (
        "property_id String, mode String, window_start Date, window_end Date, loaded_at DateTime64(6, 'UTC')"
    ),
    "google_ads___campaigns": (
        "source String, customer_id String, currency String, time_zone String, campaign_id String, "
        "campaign_name String, campaign_status String, serving_status String, channel_type String, "
        "bidding_strategy_type String, start_date String, end_date String, budget_micros Int64"
    ),
    "google_ads___performance_ad_groups": (
        "source String, report String, customer_id String, currency String, date Date, "
        "campaign_id String, campaign_name String, ad_group_id String, ad_group_name String, "
        "ad_group_status String, impressions Int64, clicks Int64, cost_micros Int64, "
        "conversions Float64, conversions_value Float64"
    ),
    "google_ads___performance_asset_groups": (
        "source String, report String, customer_id String, currency String, date Date, "
        "campaign_id String, campaign_name String, asset_group_id String, asset_group_name String, "
        "asset_group_status String, impressions Int64, clicks Int64, cost_micros Int64, "
        "conversions Float64, conversions_value Float64"
    ),
    "google_ads___performance_campaigns": (
        "source String, report String, customer_id String, currency String, date Date, "
        "campaign_id String, campaign_name String, campaign_status String, channel_type String, "
        "impressions Int64, clicks Int64, cost_micros Int64, conversions Float64, "
        "conversions_value Float64"
    ),
    "google_ads___performance_search_terms": (
        "source String, report String, customer_id String, currency String, date Date, "
        "campaign_id String, campaign_name String, ad_group_id String, ad_group_name String, "
        "search_term String, search_term_status String, impressions Int64, clicks Int64, "
        "cost_micros Int64, conversions Float64, conversions_value Float64"
    ),
    "google_ads___sync_windows": (
        "customer_id String, mode String, window_start Date, window_end Date, loaded_at DateTime64(6, 'UTC')"
    ),
    "linkedin_ads___ad_accounts": (
        "source String, id String, name String, status String, type String, currency String, test Bool"
    ),
    "linkedin_ads___ad_analytics": (
        "source String, report String, account_id String, currency String, date Date, "
        "campaign_id String, impressions Int64, clicks Int64, cost Decimal(38, 9), conversions Int64, "
        "landing_page_clicks Int64, likes Int64, shares Int64, follows Int64"
    ),
    "linkedin_ads___ad_analytics_creatives": (
        "source String, report String, account_id String, currency String, date Date, "
        "impressions Int64, clicks Int64, cost Decimal(38, 9), conversions Int64, "
        "landing_page_clicks Int64, likes Int64, shares Int64, follows Int64, creative_id String"
    ),
    "linkedin_ads___campaign_groups": (
        "source String, id String, account_id String, name String, status String, "
        "total_budget Decimal(38, 9), daily_budget Decimal(38, 9), currency String, "
        "budget_optimization Bool, objective_type String, run_start DateTime64(6, 'UTC'), "
        "run_end DateTime64(6, 'UTC'), created_at DateTime64(6, 'UTC'), modified_at DateTime64(6, "
        "'UTC')"
    ),
    "linkedin_ads___campaigns": (
        "source String, id String, account_id String, campaign_group_id String, name String, "
        "status String, type String, objective_type String, cost_type String, "
        "optimization_target_type String, daily_budget Decimal(38, 9), unit_cost Decimal(38, 9), "
        "currency String, run_start DateTime64(6, 'UTC'), run_end DateTime64(6, 'UTC'), "
        "created_at DateTime64(6, 'UTC'), modified_at DateTime64(6, 'UTC')"
    ),
    "linkedin_ads___creatives": (
        "source String, id String, account_id String, campaign_id String, intended_status String, "
        "is_serving Bool, content_reference String, created_at DateTime64(6, 'UTC'), "
        "modified_at DateTime64(6, 'UTC')"
    ),
    "linkedin_ads___lead_form_responses": (
        "source String, id String, account_id String, lead_type String, submitted_at DateTime64(6, "
        "'UTC'), date Date, form_urn String, form_id String, campaign_id String, creative_id String, "
        "answers String, answer_count Int64"
    ),
    "linkedin_ads___sync_windows": (
        "account_id String, mode String, window_start Date, window_end Date, loaded_at DateTime64(6, 'UTC')"
    ),
    "meta_ads___ad_sets": (
        "id String, account_id String, name String, status String, effective_status String, "
        "campaign_id String, optimization_goal String, billing_event String, bid_strategy String, "
        "bid_amount String, daily_budget String, lifetime_budget String, "
        "start_time Nullable(DateTime64(6, 'UTC')), end_time Nullable(DateTime64(6, 'UTC')), "
        "created_time Nullable(DateTime64(6, 'UTC')), updated_time Nullable(DateTime64(6, 'UTC'))"
    ),
    "meta_ads___ads": (
        "id String, account_id String, name String, status String, effective_status String, "
        "adset_id String, campaign_id String, creative Nullable(String), "
        "created_time Nullable(DateTime64(6, 'UTC')), updated_time Nullable(DateTime64(6, 'UTC'))"
    ),
    "meta_ads___campaigns": (
        "id String, account_id String, name String, status String, effective_status String, "
        "objective String, buying_type String, daily_budget String, lifetime_budget String, "
        "start_time Nullable(DateTime64(6, 'UTC')), stop_time Nullable(DateTime64(6, 'UTC')), "
        "created_time Nullable(DateTime64(6, 'UTC')), updated_time Nullable(DateTime64(6, 'UTC'))"
    ),
    "meta_ads___insights_ads": (
        "source String, report String, level String, account_id String, currency String, date Date, "
        "campaign_id String, campaign_name String, adset_id String, adset_name String, ad_id String, "
        "ad_name String, impressions Int64, clicks Int64, reach Int64, spend Decimal(38, 9), "
        "frequency Float64, ctr Float64, cpc Float64, cpm Float64, actions String, "
        "action_values String"
    ),
    "meta_ads___insights_age_gender": (
        "source String, report String, level String, account_id String, currency String, date Date, "
        "campaign_id String, campaign_name String, age String, gender String, impressions Int64, "
        "clicks Int64, reach Int64, spend Decimal(38, 9), frequency Float64, ctr Float64, cpc Float64, "
        "cpm Float64, actions String, action_values String"
    ),
    "meta_ads___insights_campaigns": (
        "source String, report String, level String, account_id String, currency String, date Date, "
        "campaign_id String, campaign_name String, impressions Int64, clicks Int64, reach Int64, "
        "spend Decimal(38, 9), frequency Float64, ctr Float64, cpc Float64, cpm Float64, "
        "actions String, action_values String"
    ),
    "meta_ads___insights_countries": (
        "source String, report String, level String, account_id String, currency String, date Date, "
        "campaign_id String, campaign_name String, country String, impressions Int64, clicks Int64, "
        "reach Int64, spend Decimal(38, 9), frequency Float64, ctr Float64, cpc Float64, cpm Float64, "
        "actions String, action_values String"
    ),
    "meta_ads___insights_placements": (
        "source String, report String, level String, account_id String, currency String, date Date, "
        "campaign_id String, campaign_name String, publisher_platform String, "
        "platform_position String, impressions Int64, clicks Int64, reach Int64, spend Decimal(38, 9), "
        "frequency Float64, ctr Float64, cpc Float64, cpm Float64, actions String, "
        "action_values String"
    ),
    "meta_ads___leads": (
        "source String, account_id String, id String, created_time DateTime64(6, 'UTC'), date Date, "
        "ad_id String, ad_name String, adset_id String, adset_name String, campaign_id String, "
        "campaign_name String, form_id String, is_organic Bool, platform String, field_data String, "
        "field_count Int64"
    ),
    "meta_ads___sync_windows": (
        "account_id String, mode String, levels String, window_start Date, window_end Date, "
        "loaded_at DateTime64(6, 'UTC')"
    ),
    "search_console___core": (
        "source String, report String, site_url String, search_type String, date Date, clicks Int64, "
        "impressions Int64, ctr Float64, position Float64"
    ),
    "search_console___countries": (
        "source String, report String, site_url String, search_type String, date Date, country String, "
        "clicks Int64, impressions Int64, ctr Float64, position Float64"
    ),
    "search_console___devices": (
        "source String, report String, site_url String, search_type String, date Date, device String, "
        "clicks Int64, impressions Int64, ctr Float64, position Float64"
    ),
    "search_console___pages": (
        "source String, report String, site_url String, search_type String, date Date, page String, "
        "clicks Int64, impressions Int64, ctr Float64, position Float64"
    ),
    "search_console___queries": (
        "source String, report String, site_url String, search_type String, date Date, query String, "
        "clicks Int64, impressions Int64, ctr Float64, position Float64"
    ),
    "search_console___query_page": (
        "source String, report String, site_url String, search_type String, date Date, query String, "
        "page String, clicks Int64, impressions Int64, ctr Float64, position Float64"
    ),
    "search_console___sync_windows": (
        "site_url String, search_type String, mode String, window_start Date, window_end Date, "
        "loaded_at DateTime64(6, 'UTC')"
    ),
    "stripe___balance_transactions": (
        "source String, id String, type String, created Int64, created_at DateTime64(6, 'UTC'), "
        "available_on DateTime64(6, 'UTC'), currency String, amount Int64, fee Int64, net Int64, "
        "source_id String, reporting_category String, status String"
    ),
    "stripe___customers": (
        "source String, id String, created DateTime64(6, 'UTC'), email String, name String, "
        "currency String, delinquent Bool, deleted Bool, campaign_key String, utm_source String, "
        "utm_medium String, utm_campaign String"
    ),
    "stripe___invoices": (
        "source String, id String, customer_id String, subscription_id String, status String, "
        "created DateTime64(6, 'UTC'), paid_at DateTime64(6, 'UTC'), voided_at DateTime64(6, 'UTC'), "
        "currency String, amount_due Int64, amount_paid Int64, amount_remaining Int64, subtotal Int64, "
        "total Int64, billing_reason String, campaign_key String, utm_source String, "
        "utm_medium String, utm_campaign String"
    ),
    "stripe___subscriptions": (
        "source String, id String, customer_id String, status String, created DateTime64(6, 'UTC'), "
        "current_period_start DateTime64(6, 'UTC'), current_period_end DateTime64(6, 'UTC'), "
        "canceled_at DateTime64(6, 'UTC'), currency String, price_id String, product_id String, "
        "interval String, unit_amount Int64, quantity Int64, campaign_key String, utm_source String, "
        "utm_medium String, utm_campaign String"
    ),
    "stripe___sync_windows": (
        "mode String, invoices_since DateTime64(6, 'UTC'), loaded_at DateTime64(6, 'UTC')"
    ),
    "twenty___companies": (
        "source String, id String, name String, domain String, employees Int64, "
        "annual_recurring_revenue Decimal(38, 9), currency String, ideal_customer_profile Bool, "
        "account_owner_id String, created_at DateTime64(6, 'UTC'), updated_at DateTime64(6, 'UTC'), "
        "deleted_at DateTime64(6, 'UTC'), campaign_key String, utm_source String, utm_medium String, "
        "utm_campaign String"
    ),
    "twenty___note_targets": (
        "source String, id String, note_id String, person_id String, company_id String, "
        "opportunity_id String, created_at DateTime64(6, 'UTC'), updated_at DateTime64(6, 'UTC'), "
        "deleted_at DateTime64(6, 'UTC')"
    ),
    "twenty___notes": (
        "source String, id String, title String, body String, created_by String, "
        "created_at DateTime64(6, 'UTC'), updated_at DateTime64(6, 'UTC'), deleted_at DateTime64(6, "
        "'UTC')"
    ),
    "twenty___opportunities": (
        "source String, id String, name String, stage String, amount Decimal(38, 9), currency String, "
        "close_date DateTime64(6, 'UTC'), company_id String, person_id String, "
        "created_at DateTime64(6, 'UTC'), updated_at DateTime64(6, 'UTC'), deleted_at DateTime64(6, "
        "'UTC'), campaign_key String, utm_source String, utm_medium String, utm_campaign String"
    ),
    "twenty___people": (
        "source String, id String, first_name String, last_name String, email String, "
        "job_title String, city String, company_id String, created_at DateTime64(6, 'UTC'), "
        "updated_at DateTime64(6, 'UTC'), deleted_at DateTime64(6, 'UTC'), campaign_key String, "
        "utm_source String, utm_medium String, utm_campaign String"
    ),
    "twenty___sync_windows": ("mode String, loaded_at DateTime64(6, 'UTC')"),
    "twenty___task_targets": (
        "source String, id String, task_id String, person_id String, company_id String, "
        "opportunity_id String, created_at DateTime64(6, 'UTC'), updated_at DateTime64(6, 'UTC'), "
        "deleted_at DateTime64(6, 'UTC')"
    ),
    "twenty___tasks": (
        "source String, id String, title String, body String, status String, due_at DateTime64(6, "
        "'UTC'), assignee_id String, created_by String, created_at DateTime64(6, 'UTC'), "
        "updated_at DateTime64(6, 'UTC'), deleted_at DateTime64(6, 'UTC')"
    ),
}

_TOP_LEVEL_COMMA = re.compile(r",\s*(?![^()]*\))")


def columns(table: str) -> list[tuple[str, str]]:
    """(name, ClickHouse type) for every column of a raw table, in order."""
    found: list[tuple[str, str]] = []
    for part in _TOP_LEVEL_COMMA.split(RAW_TABLES[table]):
        name, _, kind = part.strip().partition(" ")
        found.append((name, kind))
    return found


def source_of(table: str) -> str:
    return table.split("___", 1)[0]
