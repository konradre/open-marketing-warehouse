"""Flatten Twenty's nested records (composite fields, money in micros) into warehouse rows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from dlt.common.schema.typing import TColumnSchema

from omw.sources.columns import column

SOURCE = "twenty"
ATTRIBUTION_FIELDS: dict[str, str] = {
    "campaignKey": "campaign_key",
    "utmSource": "utm_source",
    "utmMedium": "utm_medium",
    "utmCampaign": "utm_campaign",
}
"""Twenty custom fields (camelCase, as the API names them) -> warehouse columns. Optional on every object."""

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _ts(value: Any) -> datetime:
    if not value:
        return EPOCH
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _money(value: Any) -> tuple[Decimal, str]:
    if isinstance(value, Mapping):
        micros = value.get("amountMicros")
        amount = Decimal(str(micros)) / Decimal(1_000_000) if micros not in (None, "") else Decimal(0)
        return amount, str(value.get("currencyCode") or "")
    return Decimal(0), ""


def _attribution(obj: Mapping[str, Any]) -> dict[str, str]:
    return {col: str(obj.get(field) or "") for field, col in ATTRIBUTION_FIELDS.items()}


def _attribution_columns() -> dict[str, TColumnSchema]:
    return {col: column("text") for col in ATTRIBUTION_FIELDS.values()}


def shape_company(obj: Mapping[str, Any]) -> dict[str, Any]:
    domain = obj.get("domainName")
    arr, arr_currency = _money(obj.get("annualRecurringRevenue"))
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "name": str(obj.get("name") or ""),
        "domain": str(domain.get("primaryLinkUrl") or "")
        if isinstance(domain, Mapping)
        else str(domain or ""),
        "employees": int(obj.get("employees") or 0),
        "annual_recurring_revenue": arr,
        "currency": arr_currency,
        "ideal_customer_profile": bool(obj.get("idealCustomerProfile", False)),
        "account_owner_id": str(obj.get("accountOwnerId") or ""),
        "created_at": _ts(obj.get("createdAt")),
        "updated_at": _ts(obj.get("updatedAt")),
        "deleted_at": _ts(obj.get("deletedAt")),
        **_attribution(obj),
    }


COMPANY_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "name": column("text"),
    "domain": column("text"),
    "employees": column("bigint"),
    "annual_recurring_revenue": column("decimal"),
    "currency": column("text"),
    "ideal_customer_profile": column("bool"),
    "account_owner_id": column("text"),
    "created_at": column("timestamp"),
    "updated_at": column("timestamp"),
    "deleted_at": column("timestamp"),
    **_attribution_columns(),
}


def shape_person(obj: Mapping[str, Any]) -> dict[str, Any]:
    raw_name = obj.get("name")
    raw_emails = obj.get("emails")
    name: Mapping[str, Any] = raw_name if isinstance(raw_name, Mapping) else {}
    emails: Mapping[str, Any] = raw_emails if isinstance(raw_emails, Mapping) else {}
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "first_name": str(name.get("firstName") or ""),
        "last_name": str(name.get("lastName") or ""),
        "email": str(emails.get("primaryEmail") or ""),
        "job_title": str(obj.get("jobTitle") or ""),
        "city": str(obj.get("city") or ""),
        "company_id": str(obj.get("companyId") or ""),
        "created_at": _ts(obj.get("createdAt")),
        "updated_at": _ts(obj.get("updatedAt")),
        "deleted_at": _ts(obj.get("deletedAt")),
        **_attribution(obj),
    }


PERSON_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "first_name": column("text"),
    "last_name": column("text"),
    "email": column("text"),
    "job_title": column("text"),
    "city": column("text"),
    "company_id": column("text"),
    "created_at": column("timestamp"),
    "updated_at": column("timestamp"),
    "deleted_at": column("timestamp"),
    **_attribution_columns(),
}


def shape_opportunity(obj: Mapping[str, Any]) -> dict[str, Any]:
    amount, currency = _money(obj.get("amount"))
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "name": str(obj.get("name") or ""),
        "stage": str(obj.get("stage") or ""),
        "amount": amount,
        "currency": currency,
        "close_date": _ts(obj.get("closeDate")),
        "company_id": str(obj.get("companyId") or ""),
        "person_id": str(obj.get("pointOfContactId") or ""),
        "created_at": _ts(obj.get("createdAt")),
        "updated_at": _ts(obj.get("updatedAt")),
        "deleted_at": _ts(obj.get("deletedAt")),
        **_attribution(obj),
    }


OPPORTUNITY_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "name": column("text"),
    "stage": column("text"),
    "amount": column("decimal"),
    "currency": column("text"),
    "close_date": column("timestamp"),
    "company_id": column("text"),
    "person_id": column("text"),
    "created_at": column("timestamp"),
    "updated_at": column("timestamp"),
    "deleted_at": column("timestamp"),
    **_attribution_columns(),
}
