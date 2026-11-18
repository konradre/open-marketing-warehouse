"""The shared campaign keys, read back from the loaded warehouse: one line per key with what each source
holds under it. This is the join the cross-source measures (ROAS, CAC) rest on."""

from __future__ import annotations

from typing import Any

from omw.checks.results import Warehouse
from omw.metrics.campaign_keys import list_campaign_keys

AD_SOURCES = ("meta_ads", "google_ads", "linkedin_ads")
COLUMNS = ["campaign_key", *AD_SOURCES, "ga4 sessions", "stripe customers", "twenty opportunities"]


def _by_key(client: Warehouse, sql: str) -> dict[str, int]:
    return {str(key): int(value) for key, value in client.query(sql).result_rows}


def shared_keys(client: Warehouse) -> tuple[list[str], list[list[Any]]]:
    """(columns, rows): each campaign key, the campaign that maps to it on each ad platform (a ``*`` marks
    a key set by hand), and the GA4 sessions, Stripe customers and Twenty opportunities carrying it."""
    names: dict[str, dict[str, str]] = {}
    for source, _native_id, key, name, origin in list_campaign_keys(client):
        names.setdefault(str(key), {})[str(source)] = f"{name}{' *' if origin == 'manual' else ''}"
    sessions = _by_key(client, "SELECT campaign, sum(sessions) FROM marts.ga4_campaigns GROUP BY campaign")
    customers = _by_key(
        client, "SELECT campaign_key, uniqExact(customer_id) FROM marts.stripe_revenue GROUP BY campaign_key"
    )
    deals = _by_key(client, "SELECT campaign_key, count() FROM marts.twenty_pipeline GROUP BY campaign_key")
    rows = [
        [
            key,
            *(platforms.get(source, "-") for source in AD_SOURCES),
            sessions.get(key, 0),
            customers.get(key, 0),
            deals.get(key, 0),
        ]
        for key, platforms in sorted(names.items())
    ]
    rows.append(["(no key)", "-", "-", "-", "-", customers.get("", 0), deals.get("", 0)])
    return COLUMNS, rows
