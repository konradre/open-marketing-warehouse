"""Google Ads source (docs/THREADS.md T4): GAQL reports through ``GoogleAdsService.SearchStream`` (REST).

Daily performance at ``campaign`` and ``ad_group`` level (impressions, clicks, cost_micros, conversions,
conversions_value) plus ``campaign`` entities. Each run re-fetches a rolling 30-day window for conversion
lag and replaces the ``(customer_id, date)`` slice. ``cost_micros`` stays raw; the marts views turn it into
a currency amount with the account currency.
"""

from omw.sources.google_ads.client import GoogleAdsClient, GoogleAdsQueryError, oauth_token_provider
from omw.sources.google_ads.queries import REPORTS, Report, entities_query, performance_query
from omw.sources.google_ads.rows import shape_entity, shape_performance
from omw.sources.google_ads.source import RE_PULL_DAYS, SOURCE_NAME, google_ads

__all__ = [
    "RE_PULL_DAYS",
    "REPORTS",
    "SOURCE_NAME",
    "GoogleAdsClient",
    "GoogleAdsQueryError",
    "Report",
    "entities_query",
    "google_ads",
    "oauth_token_provider",
    "performance_query",
    "shape_entity",
    "shape_performance",
]
