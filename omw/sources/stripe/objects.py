"""Flatten Stripe objects into warehouse rows: minor-unit amounts, epoch seconds to timestamps, and the
attribution keys lifted out of ``metadata``."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from dlt.common.schema.typing import TColumnSchema

from omw.sources.columns import column

SOURCE = "stripe"

ATTRIBUTION_KEYS: tuple[str, ...] = ("campaign_key", "utm_source", "utm_medium", "utm_campaign")
"""Metadata keys the pipeline lifts into columns. ``campaign_key`` is the warehouse-wide key (the marts
mapping table, DESIGN.md §6); the ``utm_*`` keys are what most sign-up flows write."""


def _ts(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value), tz=UTC) if value else datetime(1970, 1, 1, tzinfo=UTC)


def _id(value: Any) -> str:
    """Stripe may expand a reference into an object; keep the id either way."""
    if isinstance(value, Mapping):
        return str(value.get("id", ""))
    return str(value) if value else ""


def _int(value: Any) -> int:
    return int(value) if value not in (None, "") else 0


def _attribution(obj: Mapping[str, Any]) -> dict[str, str]:
    metadata = obj.get("metadata") or {}
    return {key: str(metadata.get(key, "")) for key in ATTRIBUTION_KEYS}


def _attribution_columns() -> dict[str, TColumnSchema]:
    return {key: column("text") for key in ATTRIBUTION_KEYS}


def shape_customer(obj: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "created": _ts(obj.get("created")),
        "email": str(obj.get("email") or ""),
        "name": str(obj.get("name") or ""),
        "currency": str(obj.get("currency") or ""),
        "delinquent": bool(obj.get("delinquent", False)),
        "deleted": bool(obj.get("deleted", False)),
        **_attribution(obj),
    }


CUSTOMER_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "created": column("timestamp"),
    "email": column("text"),
    "name": column("text"),
    "currency": column("text"),
    "delinquent": column("bool"),
    "deleted": column("bool"),
    **_attribution_columns(),
}


def shape_subscription(obj: Mapping[str, Any]) -> dict[str, Any]:
    items = (obj.get("items") or {}).get("data") or []
    first = items[0] if items else {}
    price = first.get("price") or {}
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "customer_id": _id(obj.get("customer")),
        "status": str(obj.get("status") or ""),
        "created": _ts(obj.get("created")),
        "current_period_start": _ts(obj.get("current_period_start")),
        "current_period_end": _ts(obj.get("current_period_end")),
        "canceled_at": _ts(obj.get("canceled_at")),
        "currency": str(obj.get("currency") or price.get("currency") or ""),
        "price_id": _id(price.get("id")),
        "product_id": _id(price.get("product")),
        "interval": str((price.get("recurring") or {}).get("interval") or ""),
        "unit_amount": _int(price.get("unit_amount")),
        "quantity": _int(first.get("quantity")),
        **_attribution(obj),
    }


SUBSCRIPTION_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "customer_id": column("text"),
    "status": column("text"),
    "created": column("timestamp"),
    "current_period_start": column("timestamp"),
    "current_period_end": column("timestamp"),
    "canceled_at": column("timestamp"),
    "currency": column("text"),
    "price_id": column("text"),
    "product_id": column("text"),
    "interval": column("text"),
    "unit_amount": column("bigint"),
    "quantity": column("bigint"),
    **_attribution_columns(),
}


def shape_invoice(obj: Mapping[str, Any]) -> dict[str, Any]:
    transitions = obj.get("status_transitions") or {}
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "customer_id": _id(obj.get("customer")),
        "subscription_id": _id(obj.get("subscription")),
        "status": str(obj.get("status") or ""),
        "created": _ts(obj.get("created")),
        "paid_at": _ts(transitions.get("paid_at")),
        "voided_at": _ts(transitions.get("voided_at")),
        "currency": str(obj.get("currency") or ""),
        "amount_due": _int(obj.get("amount_due")),
        "amount_paid": _int(obj.get("amount_paid")),
        "amount_remaining": _int(obj.get("amount_remaining")),
        "subtotal": _int(obj.get("subtotal")),
        "total": _int(obj.get("total")),
        "billing_reason": str(obj.get("billing_reason") or ""),
        **_attribution(obj),
    }


INVOICE_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "customer_id": column("text"),
    "subscription_id": column("text"),
    "status": column("text"),
    "created": column("timestamp"),
    "paid_at": column("timestamp"),
    "voided_at": column("timestamp"),
    "currency": column("text"),
    "amount_due": column("bigint"),
    "amount_paid": column("bigint"),
    "amount_remaining": column("bigint"),
    "subtotal": column("bigint"),
    "total": column("bigint"),
    "billing_reason": column("text"),
    **_attribution_columns(),
}


def shape_balance_transaction(obj: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "type": str(obj.get("type") or ""),
        "created": int(obj.get("created") or 0),
        "created_at": _ts(obj.get("created")),
        "available_on": _ts(obj.get("available_on")),
        "currency": str(obj.get("currency") or ""),
        "amount": _int(obj.get("amount")),
        "fee": _int(obj.get("fee")),
        "net": _int(obj.get("net")),
        "source_id": _id(obj.get("source")),
        "reporting_category": str(obj.get("reporting_category") or ""),
        "status": str(obj.get("status") or ""),
    }


BALANCE_TRANSACTION_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "type": column("text"),
    "created": column("bigint"),
    "created_at": column("timestamp"),
    "available_on": column("timestamp"),
    "currency": column("text"),
    "amount": column("bigint"),
    "fee": column("bigint"),
    "net": column("bigint"),
    "source_id": column("text"),
    "reporting_category": column("text"),
    "status": column("text"),
}
