"""Stripe source (docs/THREADS.md T5): mutable objects merged, immutable ones incremental.

``customers`` and ``subscriptions`` are listed in full and merged on ``id``; ``invoices`` created inside
a rolling window are merged on ``id`` (status and amounts change until paid or voided);
``balance_transactions`` are immutable and append incrementally on ``created``. Amounts stay in Stripe's
minor units; the marts views turn them into currency amounts. Attribution metadata (``campaign_key``,
``utm_*``) is lifted from ``metadata`` on every object so revenue can be joined to a campaign key.

Patterns re-authored from dlt's ``stripe_analytics`` verified source (Apache-2.0, see NOTICE).
"""

from omw.sources.stripe.client import StripeAccessError, StripeClient
from omw.sources.stripe.objects import (
    ATTRIBUTION_KEYS,
    shape_balance_transaction,
    shape_customer,
    shape_invoice,
    shape_subscription,
)
from omw.sources.stripe.source import INVOICE_WINDOW_DAYS, SOURCE_NAME, stripe

__all__ = [
    "ATTRIBUTION_KEYS",
    "INVOICE_WINDOW_DAYS",
    "SOURCE_NAME",
    "StripeAccessError",
    "StripeClient",
    "shape_balance_transaction",
    "shape_customer",
    "shape_invoice",
    "shape_subscription",
    "stripe",
]
