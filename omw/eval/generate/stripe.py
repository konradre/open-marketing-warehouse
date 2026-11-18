"""Stripe: customers, subscriptions, invoices and balance transactions.

Attribution metadata sits where it does in real accounts: sometimes on the invoice, sometimes on the
subscription, sometimes only on the customer, and now and then the customer and the subscription disagree.
The ``marts`` rows are resolved here in Python (invoice, then subscription, then customer) and the raw rows
keep the metadata where it was put, so the view's own resolution is checked against an independent one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from omw.eval.generate.rng import Rng
from omw.eval.world import END, START, World, days

EPOCH = datetime(1970, 1, 1)
FIELDS = ("campaign_key", "utm_source", "utm_medium", "utm_campaign")
NONE = ("", "", "", "")
NEWSLETTER = ("", "newsletter", "email", "monthly_newsletter")

# campaign key -> (utm_source, utm_medium); utm_campaign repeats the key
CHANNEL_OF: dict[str, tuple[str, str]] = {
    "brand_search": ("google", "cpc"),
    "widgets_prospecting": ("google", "cpc"),
    "widgets_retargeting": ("facebook", "paid_social"),
    "gadgets_launch": ("facebook", "paid_social"),
    "lead_gen_ebook": ("linkedin", "paid_social"),
    "abm_tier_1": ("linkedin", "paid_social"),
    "emea_generic": ("google", "cpc"),
    "emea_brand": ("google", "cpc"),
    "partner_referral": ("partner.example", "referral"),
}
USD_KEYS = (
    ("widgets_prospecting", 0.22),
    ("brand_search", 0.15),
    ("gadgets_launch", 0.12),
    ("widgets_retargeting", 0.09),
    ("lead_gen_ebook", 0.05),
    ("abm_tier_1", 0.03),
    ("partner_referral", 0.04),
    ("newsletter", 0.07),
    ("", 0.23),
)
EUR_KEYS = (("emea_generic", 0.45), ("emea_brand", 0.22), ("newsletter", 0.05), ("", 0.28))
PLANS = {"USD": ((4900, 0.5), (9900, 0.35), (24900, 0.15)), "EUR": ((4500, 0.5), (8900, 0.35), (22900, 0.15))}


def attribution(key: str) -> tuple[str, str, str, str]:
    if key == "":
        return NONE
    if key == "newsletter":
        return NEWSLETTER
    source, medium = CHANNEL_OF[key]
    return (key, source, medium, key)


def resolve(*levels: tuple[str, str, str, str]) -> tuple[str, str, str, str]:
    """Each field on its own: the first level that has it."""
    return tuple(next((level[i] for level in levels if level[i]), "") for i in range(4))  # type: ignore[return-value]


@dataclass
class Customer:
    number: int
    created: datetime
    currency: str
    amount: int
    """Monthly plan price in minor units."""
    on_customer: tuple[str, str, str, str]
    on_subscription: tuple[str, str, str, str]
    on_invoices: tuple[str, str, str, str]
    trial: bool
    canceled: date | None

    @property
    def id(self) -> str:
        return f"cus_E{self.number:05d}"

    @property
    def subscription_id(self) -> str:
        return f"sub_E{self.number:05d}"


def _customer(rng: Rng, number: int, created: datetime) -> Customer:
    currency = "EUR" if rng.chance(0.24, "stripe", number, "currency") else "USD"
    key = rng.weighted(USD_KEYS if currency == "USD" else EUR_KEYS, "stripe", number, "key")
    truth = attribution(key)
    level = rng.weighted(
        (("customer", 0.5), ("subscription", 0.3), ("invoice", 0.2)), "stripe", number, "level"
    )
    on_customer = truth if level == "customer" else NONE
    if level == "subscription" and truth != NONE and rng.chance(0.3, "stripe", number, "conflict"):
        # The customer signed up through one campaign and the subscription was attributed to another.
        on_customer = attribution("brand_search" if currency == "USD" else "emea_brand")
    canceled = None
    if rng.chance(0.1, "stripe", number, "churn"):
        canceled = created.date() + timedelta(days=rng.integer(35, 100, "stripe", number, "churn_after"))
    return Customer(
        number=number,
        created=created,
        currency=currency,
        amount=rng.weighted(PLANS[currency], "stripe", number, "plan"),
        on_customer=on_customer,
        on_subscription=truth if level == "subscription" else NONE,
        on_invoices=truth if level == "invoice" else NONE,
        trial=rng.chance(0.06, "stripe", number, "trial"),
        canceled=canceled,
    )


def customers(rng: Rng) -> list[Customer]:
    found: list[Customer] = []
    for number in range(60):  # signed up before the window; they renew inside it
        created = datetime(2026, 1, 3, 10, 0) + timedelta(days=number * 2, hours=number % 9)
        found.append(_customer(rng, number, created))
    number = 60
    for day in days():
        for slot in range(int(rng.between(0, 2.3, "stripe", day, "signups"))):
            hour = rng.integer(1, 21, "stripe", day, slot, "hour")
            found.append(_customer(rng, number, datetime(day.year, day.month, day.day, hour, 15)))
            number += 1
    return found


def _renewals(customer: Customer) -> list[date]:
    """Renewal days inside the window: the sign-up day of each later month (capped at the 28th)."""
    found: list[date] = []
    year, month = customer.created.year, customer.created.month
    while True:
        month += 1
        if month > 12:
            year, month = year + 1, 1
        day = date(year, month, min(customer.created.day, 28))
        if day > END or (customer.canceled and day >= customer.canceled):
            return found
        if day >= START:
            found.append(day)


def _fees(amount: int) -> int:
    return round(amount * 0.029) + 30


def build_stripe(rng: Rng, world: World) -> None:
    everyone = customers(rng)
    invoice_number = 0

    def invoice(
        customer: Customer, paid: datetime, amount: int, reason: str, status: str, subscription: bool = True
    ) -> None:
        nonlocal invoice_number
        invoice_number += 1
        invoice_id = f"in_E{invoice_number:06d}"
        amount_paid = amount if status == "paid" else 0
        own = customer.on_invoices
        world.add_raw(
            "stripe___invoices",
            [
                {
                    "source": "stripe",
                    "id": invoice_id,
                    "customer_id": customer.id,
                    "subscription_id": customer.subscription_id if subscription else "",
                    "status": status,
                    "created": paid - timedelta(hours=1),
                    "paid_at": paid if status == "paid" else EPOCH,
                    "voided_at": paid if status == "void" else EPOCH,
                    "currency": customer.currency.lower(),
                    "amount_due": amount,
                    "amount_paid": amount_paid,
                    "amount_remaining": amount - amount_paid,
                    "subtotal": amount,
                    "total": amount,
                    "billing_reason": reason,
                    **dict(zip(FIELDS, own, strict=True)),
                }
            ],
        )
        if status != "paid" or amount_paid <= 0:
            return
        levels = (
            [own, customer.on_subscription, customer.on_customer]
            if subscription
            else [own, customer.on_customer]
        )
        resolved = resolve(*levels)
        world.add(
            "stripe_revenue",
            [
                {
                    "source": "stripe",
                    "report": "invoices",
                    "date": paid.date(),
                    "invoice_id": invoice_id,
                    "customer_id": customer.id,
                    "subscription_id": customer.subscription_id if subscription else "",
                    "currency": customer.currency,
                    "amount": Decimal(amount) / 100,
                    "billing_reason": reason,
                    **dict(zip(FIELDS, resolved, strict=True)),
                    "attributed": any(resolved),
                }
            ],
        )
        _balance(
            world, f"ch_{invoice_id}", "charge", "charge", paid, customer.currency, amount, _fees(amount)
        )
        draw = rng.unit("stripe", invoice_id, "refund")
        refunded = paid + timedelta(days=rng.integer(1, 20, "stripe", invoice_id, "refund_after"))
        if refunded.date() > END:
            return
        if draw < 0.04:
            _balance(world, f"re_{invoice_id}", "refund", "refund", refunded, customer.currency, -amount, 0)
        elif draw < 0.055:
            _balance(
                world, f"re_{invoice_id}", "refund", "refund", refunded, customer.currency, -(amount // 2), 0
            )
        elif draw < 0.062:
            _balance(
                world, f"dp_{invoice_id}", "adjustment", "dispute", refunded, customer.currency, -amount, 1500
            )

    for customer in everyone:
        signup = customer.created
        if signup.date() >= START:
            invoice(customer, signup, 0 if customer.trial else customer.amount, "subscription_create", "paid")
        for day in _renewals(customer):
            status = rng.weighted(
                (("paid", 0.95), ("open", 0.03), ("void", 0.02)), "stripe", customer.number, day
            )
            hour = rng.integer(1, 21, "stripe", customer.number, day, "hour")
            paid = datetime(day.year, day.month, day.day, hour, 5)
            invoice(customer, paid, customer.amount, "subscription_cycle", status)
        _entities(world, customer)

    for day in days():  # one-off invoices: services, overages
        if not rng.chance(0.22, "stripe", day, "manual"):
            continue
        eligible = [c for c in everyone if c.created.date() < day]
        customer = rng.pick(eligible, "stripe", day, "manual_customer")
        amount = rng.integer(10, 100, "stripe", day, "manual_amount") * 5000
        invoice(customer, datetime(day.year, day.month, day.day, 14, 30), amount, "manual", "paid", False)

    for week, day in enumerate(d for d in days() if d.weekday() == 0):  # payouts never count as revenue
        for currency in ("USD", "EUR"):
            paid = datetime(day.year, day.month, day.day, 3, 0)
            _balance(
                world,
                f"po_E{week:03d}{currency}",
                "payout",
                "payout",
                paid,
                currency,
                -(500000 + week * 1000),
                0,
            )
    for month in (6, 7, 8, 9):
        _balance(
            world, f"fee_E{month:02d}", "stripe_fee", "fee", datetime(2026, month, 1, 2, 0), "USD", -1000, 0
        )


def _balance(
    world: World, source_id: str, kind: str, category: str, at: datetime, currency: str, amount: int, fee: int
) -> None:
    transaction = f"txn_{source_id}"
    world.add_raw(
        "stripe___balance_transactions",
        [
            {
                "source": "stripe",
                "id": transaction,
                "type": kind,
                "created": int((at - EPOCH).total_seconds()),
                "created_at": at,
                "available_on": at + timedelta(days=2),
                "currency": currency.lower(),
                "amount": amount,
                "fee": fee,
                "net": amount - fee,
                "source_id": source_id,
                "reporting_category": category,
                "status": "available",
            }
        ],
    )
    world.add(
        "stripe_balance",
        [
            {
                "source": "stripe",
                "report": "balance",
                "date": at.date(),
                "transaction_id": transaction,
                "type": kind,
                "reporting_category": category,
                "status": "available",
                "currency": currency,
                "gross": Decimal(amount) / 100,
                "fee": Decimal(fee) / 100,
                "net": Decimal(amount - fee) / 100,
            }
        ],
    )


def _entities(world: World, customer: Customer) -> None:
    canceled = datetime.combine(customer.canceled, datetime.min.time()) if customer.canceled else EPOCH
    world.add_raw(
        "stripe___customers",
        [
            {
                "source": "stripe",
                "id": customer.id,
                "created": customer.created,
                "email": f"buyer{customer.number}@customers.example",
                "name": f"Customer {customer.number}",
                "currency": customer.currency.lower(),
                "delinquent": False,
                "deleted": False,
                **dict(zip(FIELDS, customer.on_customer, strict=True)),
            }
        ],
    )
    world.add_raw(
        "stripe___subscriptions",
        [
            {
                "source": "stripe",
                "id": customer.subscription_id,
                "customer_id": customer.id,
                "status": "canceled" if customer.canceled else "active",
                "created": customer.created,
                "current_period_start": customer.created,
                "current_period_end": customer.created,
                "canceled_at": canceled,
                "currency": customer.currency.lower(),
                "price_id": f"price_E{customer.amount}",
                "product_id": "prod_Ewidgets",
                "interval": "month",
                "unit_amount": customer.amount,
                "quantity": 1,
                **dict(zip(FIELDS, customer.on_subscription, strict=True)),
            }
        ],
    )
    resolved = resolve(customer.on_subscription, customer.on_customer)
    world.add(
        "stripe_subscriptions",
        [
            {
                "source": "stripe",
                "subscription_id": customer.subscription_id,
                "customer_id": customer.id,
                "status": "canceled" if customer.canceled else "active",
                "currency": customer.currency,
                "unit_amount": Decimal(customer.amount) / 100,
                **dict(zip(FIELDS, resolved, strict=True)),
            }
        ],
    )
