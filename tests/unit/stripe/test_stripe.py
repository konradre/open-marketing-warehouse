"""Stripe: client paging and errors, object shaping, source hints, incremental cursor on dummy."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import dlt
import pytest
import responses

from omw.pipelines.stripe import run_daily
from omw.sources.stripe import ATTRIBUTION_KEYS, StripeAccessError, StripeClient, stripe
from omw.sources.stripe.client import API_BASE, default_session
from omw.sources.stripe.objects import (
    INVOICE_COLUMNS,
    shape_balance_transaction,
    shape_customer,
    shape_invoice,
    shape_subscription,
)
from tests.unit.stripe.conftest import FIXTURES, FakeStripeClient, fixture_data

TODAY = date(2026, 10, 5)


def _client() -> StripeClient:
    return StripeClient("rk_test_x", session=default_session(max_attempts=3, backoff_factor=0.0, timeout=5))


def _body(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


@responses.activate
def test_follows_starting_after_until_has_more_is_false() -> None:
    responses.get(f"{API_BASE}/customers", body=_body("customers_page_1"), content_type="application/json")
    responses.get(f"{API_BASE}/customers", body=_body("customers_page_2"), content_type="application/json")
    rows = list(_client().list("customers"))
    assert [r["id"] for r in rows] == ["cus_T50000000000001", "cus_T50000000000002", "cus_T50000000000003"]
    first, second = (c.request for c in responses.calls)
    assert "limit=100" in str(first.url) and "starting_after" not in str(first.url)
    assert "starting_after=cus_T50000000000002" in str(second.url)
    assert first.headers["Authorization"] == "Bearer rk_test_x" and first.headers["Stripe-Version"]


@responses.activate
def test_errors() -> None:
    responses.get(
        f"{API_BASE}/invoices", body=_body("error_unauthorized"), status=401, content_type="application/json"
    )
    with pytest.raises(StripeAccessError, match="restricted key"):
        list(_client().list("invoices"))
    responses.get(
        f"{API_BASE}/subscriptions",
        body=_body("error_rate_limited"),
        status=429,
        content_type="application/json",
    )
    responses.get(f"{API_BASE}/subscriptions", body=_body("subscriptions"), content_type="application/json")
    assert len(list(_client().list("subscriptions", {"status": "all"}))) == 3
    assert len(responses.calls) == 3


def test_shaping_lifts_attribution_and_keeps_minor_units() -> None:
    customer = shape_customer(fixture_data("customers_page_1")[0])
    assert customer["campaign_key"] == "meta:23850000000000001" and customer["utm_source"] == "facebook"
    assert customer["created"] == datetime(2026, 9, 1, tzinfo=UTC)
    plain = shape_customer(fixture_data("customers_page_2")[0])
    assert all(plain[key] == "" for key in ATTRIBUTION_KEYS)
    sub = shape_subscription(fixture_data("subscriptions")[1])
    assert (
        sub["customer_id"] == "cus_T50000000000002"
        and sub["unit_amount"] == 9900
        and sub["interval"] == "month"
    )
    assert sub["campaign_key"] == "google:20010002" and sub["canceled_at"] == datetime(1970, 1, 1, tzinfo=UTC)
    invoice = shape_invoice(fixture_data("invoices")[2])
    assert (
        invoice["subscription_id"] == "" and invoice["amount_paid"] == 25000 and invoice["status"] == "paid"
    )
    assert invoice["paid_at"] > invoice["created"] and invoice["campaign_key"] == "search_console:example"
    assert set(invoice) == set(INVOICE_COLUMNS)
    open_invoice = shape_invoice(fixture_data("invoices")[3])
    assert open_invoice["status"] == "open" and open_invoice["amount_paid"] == 0
    txn = shape_balance_transaction(fixture_data("balance_transactions")[0])
    assert txn["net"] == txn["amount"] - txn["fee"] and isinstance(txn["created"], int)


def test_source_hints(fake_client: FakeStripeClient) -> None:
    source = stripe(client=fake_client, today=TODAY)
    assert set(source.resources) == {
        "customers",
        "subscriptions",
        "invoices",
        "balance_transactions",
        "sync_windows",
    }
    invoices: dict[str, Any] = dict(source.resources["invoices"].compute_table_schema())
    assert invoices["write_disposition"] == "merge"
    assert [c for c, h in invoices["columns"].items() if h.get("primary_key")] == ["id"]
    txns: dict[str, Any] = dict(source.resources["balance_transactions"].compute_table_schema())
    assert txns["write_disposition"] == "append" and txns["x-clickhouse-sort"] == ["id"]
    assert [c for c, h in txns["columns"].items() if h.get("primary_key")] == ["id"]
    assert list(source.resources["customers"]) and len(list(source.resources["invoices"])) == 4
    assert len(list(source.resources["subscriptions"])) == 3
    subscription_call = next(p for r, p in fake_client.calls if r == "subscriptions")
    assert subscription_call == {"status": "all"}
    invoice_call = next(p for r, p in fake_client.calls if r == "invoices")
    assert invoice_call["created[gte]"] == int(datetime(2026, 7, 7, tzinfo=UTC).timestamp())
    with pytest.raises(ValueError, match="api_key"):
        stripe(api_key=None)


def _pipeline(tmp_path: Path) -> dlt.Pipeline:
    return dlt.pipeline(
        pipeline_name="stripe_unit",
        destination=dlt.destinations.dummy(completed_prob=1.0),
        dataset_name="stripe",
        pipelines_dir=str(tmp_path),
    )


def test_balance_transactions_are_incremental_across_runs(
    tmp_path: Path, fake_client: FakeStripeClient
) -> None:
    pipeline = _pipeline(tmp_path)
    counts = run_daily(pipeline, today=TODAY, client=fake_client, marts_sql=None)
    assert counts["balance_transactions"] == 3 and counts["invoices"] == 4 and counts["customers"] == 3
    first_call = next(p for r, p in fake_client.calls if r == "balance_transactions")
    assert first_call["created[gte]"] == int(datetime(2020, 1, 1, tzinfo=UTC).timestamp())

    fake_client.overrides["balance_transactions"] = "balance_transactions_later"
    counts = run_daily(pipeline, today=TODAY, client=fake_client, marts_sql=None)
    second_call = [p for r, p in fake_client.calls if r == "balance_transactions"][-1]
    assert (
        second_call["created[gte]"] == fixture_data("balance_transactions")[0]["created"]
    )  # the newest seen
    assert counts.get("balance_transactions", 0) == 1  # the already-seen boundary row is deduplicated
