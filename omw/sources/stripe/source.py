"""The dlt source: merge customers, subscriptions and recent invoices; append balance transactions."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import dlt
from dlt.destinations.adapters import clickhouse_adapter
from dlt.extract import DltResource

from omw.sources.columns import column
from omw.sources.stripe.client import StripeClient
from omw.sources.stripe.objects import (
    BALANCE_TRANSACTION_COLUMNS,
    CUSTOMER_COLUMNS,
    INVOICE_COLUMNS,
    SUBSCRIPTION_COLUMNS,
    shape_balance_transaction,
    shape_customer,
    shape_invoice,
    shape_subscription,
)

SOURCE_NAME = "stripe"
INVOICE_WINDOW_DAYS = 90
"""Invoices created in the last 90 days are re-fetched and merged: that is how long they realistically
keep changing (open → paid, voided, uncollectible). Older ones are already settled."""
BALANCE_TRANSACTIONS_START = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp())
"""Epoch seconds the incremental cursor starts from on a first run."""


def _merge_resource(name: str, rows: Any, columns: dict[str, Any]) -> DltResource:
    resource = dlt.resource(rows, name=name, write_disposition="merge", primary_key="id", columns=columns)
    return clickhouse_adapter(resource, sort=["id"])


@dlt.source(name=SOURCE_NAME, max_table_nesting=0)
def stripe(
    api_key: str | None = dlt.secrets.value,
    invoice_window_days: int = INVOICE_WINDOW_DAYS,
    client: StripeClient | None = None,
    today: date | None = None,
) -> Iterator[DltResource]:
    """Stripe customers, subscriptions, recent invoices and balance transactions.

    ``client`` is injectable for tests; otherwise a REST client from ``api_key`` (a restricted key with
    read access to the four objects).
    """
    if client is None:
        if not api_key:
            raise ValueError("stripe needs api_key (sources.stripe.api_key)")
        client = StripeClient(api_key)
    stripe_client = client
    now = datetime.combine(today or date.today(), datetime.min.time(), tzinfo=UTC)
    invoices_since = int((now - timedelta(days=invoice_window_days)).timestamp())

    def customers() -> Iterator[dict[str, Any]]:
        for obj in stripe_client.list("customers"):
            yield shape_customer(obj)

    def subscriptions() -> Iterator[dict[str, Any]]:
        for obj in stripe_client.list("subscriptions", {"status": "all"}):
            yield shape_subscription(obj)

    def invoices() -> Iterator[dict[str, Any]]:
        for obj in stripe_client.list("invoices", {"created[gte]": invoices_since}):
            yield shape_invoice(obj)

    @dlt.resource(
        name="balance_transactions",
        write_disposition="append",
        primary_key="id",
        columns=BALANCE_TRANSACTION_COLUMNS,
    )
    def balance_transactions(
        created: dlt.sources.incremental[int] = dlt.sources.incremental(  # noqa: B008 - dlt's cursor idiom
            "created", initial_value=BALANCE_TRANSACTIONS_START
        ),
    ) -> Iterator[dict[str, Any]]:
        # Immutable, so only the ones since the last run; Stripe lists newest first, dlt tracks the max.
        for obj in stripe_client.list("balance_transactions", {"created[gte]": created.last_value}):
            yield shape_balance_transaction(obj)

    yield _merge_resource("customers", customers, CUSTOMER_COLUMNS)
    yield _merge_resource("subscriptions", subscriptions, SUBSCRIPTION_COLUMNS)
    yield _merge_resource("invoices", invoices, INVOICE_COLUMNS)
    # ClickHouse needs the primary key (id, which the incremental dedup relies on) to lead the sorting key.
    yield clickhouse_adapter(balance_transactions, sort=["id"])

    def sync_windows() -> Iterator[dict[str, Any]]:
        yield {
            "mode": "daily",
            "invoices_since": datetime.fromtimestamp(invoices_since, tz=UTC),
            "loaded_at": datetime.now(tz=UTC),
        }

    yield dlt.resource(
        sync_windows,
        name="sync_windows",
        write_disposition="append",
        columns={
            "mode": column("text"),
            "invoices_since": column("timestamp"),
            "loaded_at": column("timestamp"),
        },
    )
