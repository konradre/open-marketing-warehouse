"""Stripe as its list endpoints answer: customers, subscriptions, invoices and balance transactions.

Every paying customer of the funnel gets a subscription billed every 30 days. Most invoices are paid at
once; some are paid days later after a retry, and a few never are. A snapshot shows each object in the
state it had that morning, so the same invoice is ``open`` in one snapshot and ``paid`` in the next: that is
what the pipeline's merge on ``id`` has to absorb. Attribution lives in customer metadata (for deals won in
the CRM, on the subscription), never on the invoice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from omw.demo.fixtures.common import Payloads, epoch
from omw.demo.funnel import Funnel, Signup
from omw.demo.world import instant
from omw.eval.generate.rng import Rng

CYCLE = timedelta(days=30)
AVAILABLE_AFTER = timedelta(days=2)
"""How long a charge stays ``pending`` in the Stripe balance."""
GIVE_UP = timedelta(days=7)
"""Stripe marks an invoice uncollectible when the retries run out."""


@dataclass(frozen=True)
class Bill:
    """One invoice of a subscription, with what became of it."""

    cycle: int
    created: datetime
    paid_at: datetime | None
    """When it was paid; ``None`` when it never is."""
    refunded_at: datetime | None


def bills(rng: Rng, signup: Signup, horizon: datetime) -> tuple[list[Bill], datetime | None]:
    """Every invoice of a customer up to ``horizon``, and when the subscription was cancelled, if it was."""
    found: list[Bill] = []
    created = signup.at
    cycle = 0
    while created < horizon:
        key = ("bill", signup.number, cycle)
        draw = rng.unit(*key, "outcome")
        paid: datetime | None
        if draw < 0.86:
            paid = created + timedelta(seconds=rng.integer(20, 600, *key, "s"))
        elif draw < 0.96:
            paid = created + timedelta(days=rng.integer(1, 3, *key, "d"), hours=rng.integer(1, 20, *key, "h"))
        else:
            paid = None
        refunded = None
        if paid is not None and rng.chance(0.02, *key, "refund"):
            refunded = paid + timedelta(days=rng.integer(3, 10, *key, "refund-d"), hours=5)
        found.append(Bill(cycle, created, paid, refunded))
        if paid is None:
            break  # the subscription stops billing once an invoice goes unpaid
        created += CYCLE
        cycle += 1
        if rng.chance(0.07, "bill", signup.number, cycle, "churn"):
            # Cancelled some days before the renewal it would have paid: known whenever the snapshot is taken.
            notice = rng.integer(1, 9, "bill", signup.number, cycle, "notice")
            return found, created - timedelta(days=notice)
    return found, None


def _metadata(signup: Signup, on: str) -> dict[str, str]:
    carrier = "subscription" if signup.via == "sales" else "customer"
    if not signup.attributed or on != carrier:
        return {}
    return {
        "campaign_key": signup.key,
        "utm_source": signup.utm_source,
        "utm_medium": signup.utm_medium,
        "utm_campaign": signup.key,
    }


def _invoice(signup: Signup, bill: Bill, now: datetime, ident: str) -> dict[str, Any]:
    amount = signup.unit_amount * signup.quantity
    paid = bill.paid_at is not None and bill.paid_at < now
    lapsed = bill.paid_at is None and bill.created + GIVE_UP < now
    return {
        "id": f"in_DEMO{ident}{bill.cycle:02d}",
        "object": "invoice",
        "customer": f"cus_DEMO{ident}",
        "subscription": f"sub_DEMO{ident}",
        "status": "paid" if paid else "uncollectible" if lapsed else "open",
        "created": epoch(bill.created),
        "currency": signup.currency.lower(),
        "amount_due": amount,
        "amount_paid": amount if paid else 0,
        "amount_remaining": 0 if paid else amount,
        "subtotal": amount,
        "total": amount,
        "billing_reason": "subscription_cycle" if bill.cycle else "subscription_create",
        "livemode": False,
        "metadata": {},
        "status_transitions": {
            "finalized_at": epoch(bill.created),
            "marked_uncollectible_at": epoch(bill.created + GIVE_UP) if lapsed else None,
            "paid_at": epoch(bill.paid_at) if paid else None,
            "voided_at": None,
        },
    }


def _transactions(signup: Signup, bill: Bill, now: datetime, ident: str) -> list[dict[str, Any]]:
    amount = signup.unit_amount * signup.quantity
    found = []
    if bill.paid_at is not None and bill.paid_at < now:
        fee = round(amount * 0.029) + 30
        charge = f"{ident}{bill.cycle:02d}0"
        found.append(_transaction(charge, "charge", bill.paid_at, signup, amount, fee, now))
    if bill.refunded_at is not None and bill.refunded_at < now:
        refund = f"{ident}{bill.cycle:02d}1"
        found.append(_transaction(refund, "refund", bill.refunded_at, signup, -amount, 0, now))
    return found


def _transaction(
    ident: str, kind: str, at: datetime, signup: Signup, amount: int, fee: int, now: datetime
) -> dict[str, Any]:
    """``pending`` until the funds are available two days later. The pipeline loads a transaction once, so
    the warehouse keeps the status of that day; ``marts.stripe_balance`` derives the current one."""
    available = at + AVAILABLE_AFTER
    return {
        "id": f"txn_DEMO{ident}",
        "object": "balance_transaction",
        "type": kind,
        "created": epoch(at),
        "available_on": epoch(available),
        "currency": signup.currency.lower(),
        "amount": amount,
        "fee": fee,
        "net": amount - fee,
        "source": f"{'ch' if kind == 'charge' else 're'}_DEMO{ident}",
        "reporting_category": kind,
        "status": "available" if available < now else "pending",
    }


def _subscription(
    signup: Signup, found: list[Bill], cancelled: datetime | None, now: datetime, ident: str
) -> dict[str, Any]:
    last = found[-1]
    if cancelled is not None and cancelled < now:
        status = "canceled"
    elif last.paid_at is not None and last.paid_at < now:
        status = "active"
    else:
        status = "unpaid" if last.paid_at is None and last.created + GIVE_UP < now else "past_due"
    price = {
        "id": f"price_DEMO{signup.plan}{signup.currency.lower()}",
        "object": "price",
        "currency": signup.currency.lower(),
        "unit_amount": signup.unit_amount,
        "recurring": {"interval": "month", "interval_count": 1},
        "product": f"prod_DEMO{signup.plan}",
        "type": "recurring",
    }
    item = {
        "id": f"si_DEMO{ident}",
        "object": "subscription_item",
        "price": price,
        "quantity": signup.quantity,
    }
    return {
        "id": f"sub_DEMO{ident}",
        "object": "subscription",
        "customer": f"cus_DEMO{ident}",
        "status": status,
        "created": epoch(signup.at),
        "current_period_start": epoch(last.created),
        "current_period_end": epoch(last.created + CYCLE),
        "canceled_at": epoch(cancelled) if status == "canceled" else None,
        "currency": signup.currency.lower(),
        "livemode": False,
        "metadata": _metadata(signup, "subscription"),
        "items": {"object": "list", "data": [item], "has_more": False},
    }


def _listing(resource: str, data: list[dict[str, Any]]) -> dict[str, Any]:
    """A list endpoint's whole answer, newest first as Stripe returns it; the replay pages it."""
    data.sort(key=lambda obj: (obj["created"], obj["id"]), reverse=True)
    return {"object": "list", "url": f"/v1/{resource}", "has_more": False, "data": data}


def build(rng: Rng, funnel: Funnel, as_of: date) -> Payloads:
    now = instant(as_of)
    customers, subscriptions, invoices, transactions = [], [], [], []
    for signup in funnel.signups:
        if signup.at >= now:
            continue
        ident = f"{signup.number:06d}"
        found, cancelled = bills(rng, signup, now)
        slug = signup.name.lower().replace(" ", "-")
        customers.append(
            {
                "id": f"cus_DEMO{ident}",
                "object": "customer",
                "created": epoch(signup.at),
                "email": f"billing@{slug}.example",
                "name": signup.name,
                "currency": signup.currency.lower(),
                "delinquent": any(bill.paid_at is None for bill in found),
                "livemode": False,
                "metadata": _metadata(signup, "customer"),
            }
        )
        subscriptions.append(_subscription(signup, found, cancelled, now, ident))
        for bill in found:
            invoices.append(_invoice(signup, bill, now, ident))
            transactions += _transactions(signup, bill, now, ident)
    return {
        "customers": _listing("customers", customers),
        "subscriptions": _listing("subscriptions", subscriptions),
        "invoices": _listing("invoices", invoices),
        "balance_transactions": _listing("balance_transactions", transactions),
    }
