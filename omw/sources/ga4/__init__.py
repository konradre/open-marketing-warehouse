"""GA4 source (docs/THREADS.md T5): named report grains over the Data API ``runReport`` endpoint.

Grains: ``core`` (date), ``channels`` (session default channel group), ``landing_pages`` and ``campaigns``
(session campaign, source and medium). Every row carries ``source``, ``report``, ``property_id`` and
``date``. GA4 folds high-cardinality values into an ``(other)`` row; it is kept, flagged with ``is_other``,
so breakdowns still sum to ``core`` and the agent can disclose or exclude it. Each daily run re-fetches a
rolling window (GA4 revises the last days) and replaces the ``(property_id, date)`` slice.

Patterns re-authored from dlt's ``google_analytics`` verified source (Apache-2.0, see NOTICE).
"""

from omw.sources.ga4.client import GA4AccessError, GA4Client, service_account_token_provider
from omw.sources.ga4.grains import GRAINS, METRICS, OTHER, Grain, shape_row
from omw.sources.ga4.source import RE_PULL_DAYS, SOURCE_NAME, ga4

__all__ = [
    "GRAINS",
    "METRICS",
    "OTHER",
    "RE_PULL_DAYS",
    "SOURCE_NAME",
    "GA4AccessError",
    "GA4Client",
    "Grain",
    "ga4",
    "service_account_token_provider",
    "shape_row",
]
