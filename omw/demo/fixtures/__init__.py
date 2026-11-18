"""Write the synthetic company as fixture files: two snapshots of what every platform API answers.

    <out>/manifest.json                  seed, calendar, synthetic account ids, the shared campaign keys
    <out>/initial/<source>/<name>.json   every answer as of yesterday
    <out>/restated/<source>/<name>.json  every answer as of today: one more day, recent days revised

The files have the shapes of the recorded fixtures under ``tests/fixtures`` (which are the shapes the APIs
return), so the same clients read them. The output is a pure function of seed, day and history length.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from omw.demo.fixtures import google, linkedin, meta, stripe, twenty, web
from omw.demo.fixtures.common import Payloads
from omw.demo.funnel import Funnel, build_funnel
from omw.demo.world import (
    CAMPAIGN_KEYS,
    CAMPAIGNS,
    DEFAULT_DAYS,
    GA4_PROPERTY,
    GOOGLE_EUR,
    GOOGLE_MANAGER,
    GOOGLE_USD,
    LINKEDIN_ACCOUNT,
    META_ACCOUNT,
    SEED,
    SITE_URL,
    SNAPSHOTS,
    TWENTY_URL,
    Calendar,
    Snapshot,
)
from omw.eval.generate.rng import Rng

SOURCES = ("search_console", "meta_ads", "google_ads", "ga4", "stripe", "linkedin_ads", "twenty")
MANIFEST = "manifest.json"


@dataclass(frozen=True)
class Manifest:
    """What a fixture directory holds; ``omw demo load`` reads the calendar and the ids from it."""

    seed: int
    today: date
    days: int
    data: dict[str, Any]

    @property
    def calendar(self) -> Calendar:
        return Calendar(self.today, self.days)

    @classmethod
    def read(cls, directory: Path) -> Manifest:
        path = directory / MANIFEST
        if not path.exists():
            raise FileNotFoundError(f"{path} not found: run `omw demo generate --out {directory}` first")
        data = json.loads(path.read_text())
        return cls(int(data["seed"]), date.fromisoformat(data["today"]), int(data["days"]), data)


def build_snapshot(rng: Rng, funnel: Funnel, snapshot: Snapshot) -> dict[str, Payloads]:
    """Every source's fixture payloads for one snapshot, keyed by source then file name."""
    as_of = funnel.calendar.as_of(snapshot)
    return {
        "search_console": web.build_search_console(rng, funnel, as_of),
        "meta_ads": meta.build(rng, funnel, as_of),
        "google_ads": google.build(rng, funnel, as_of),
        "ga4": web.build_ga4(rng, funnel, as_of),
        "stripe": stripe.build(rng, funnel, as_of),
        "linkedin_ads": linkedin.build(rng, funnel, as_of),
        "twenty": twenty.build(funnel, as_of),
    }


def row_count(payload: Any) -> int:
    """Records in a fixture payload, whatever envelope its API uses."""
    if isinstance(payload, list):  # a searchStream body: chunks of results
        return sum(len(chunk.get("results", [])) for chunk in payload)
    for key in ("rows", "elements", "data"):
        found = payload.get(key)
        if isinstance(found, dict):  # Twenty: {"data": {"companies": [...]}}
            found = next(iter(found.values()), [])
        if isinstance(found, list):
            return len(found)
    return 1


def campaign_key_table() -> list[dict[str, Any]]:
    """Each cross-source key with the platform campaigns that map to it."""
    return [
        {
            "campaign_key": key,
            "campaigns": [
                {
                    "source": c.source,
                    "campaign_id": c.campaign_id,
                    "campaign_name": c.name,
                    "origin": "manual" if c.manual_key else "derived",
                }
                for c in CAMPAIGNS
                if c.key == key
            ],
        }
        for key in CAMPAIGN_KEYS
    ]


def generate(out: Path, today: date | None = None, days: int = DEFAULT_DAYS, seed: int = SEED) -> Manifest:
    """Write both snapshots and the manifest under ``out``, replacing fixture files already there."""
    calendar = Calendar(today or date.today(), days)
    rng = Rng(seed)
    funnel = build_funnel(rng, calendar)
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for snapshot in SNAPSHOTS:
        for source, payloads in build_snapshot(rng, funnel, snapshot).items():
            directory = out / snapshot / source
            directory.mkdir(parents=True, exist_ok=True)
            for name, payload in payloads.items():
                (directory / f"{name}.json").write_text(json.dumps(payload, indent=1) + "\n")
                counts.setdefault(snapshot, {}).setdefault(source, {})[name] = row_count(payload)
    data = {
        "seed": seed,
        "today": calendar.today.isoformat(),
        "days": days,
        "start": calendar.start.isoformat(),
        "snapshots": {name: {"as_of": calendar.as_of(name).isoformat()} for name in SNAPSHOTS},
        "accounts": {
            "search_console": {"site_urls": [SITE_URL]},
            "meta_ads": {"account_ids": [META_ACCOUNT]},
            "google_ads": {"customer_ids": [GOOGLE_USD, GOOGLE_EUR], "login_customer_id": GOOGLE_MANAGER},
            "ga4": {"property_ids": [GA4_PROPERTY]},
            "linkedin_ads": {"account_ids": [LINKEDIN_ACCOUNT]},
            "twenty": {"base_url": TWENTY_URL},
        },
        "campaign_keys": campaign_key_table(),
        "funnel": {
            "paying_customers": len(funnel.signups),
            "attributed_customers": sum(1 for signup in funnel.signups if signup.attributed),
            "leads": {source: len(found) for source, found in funnel.leads.items()},
            "crm_contacts": len(funnel.deals),
            "opportunities": sum(1 for deal in funnel.deals if deal.opened_at is not None),
            "won": sum(1 for deal in funnel.deals if deal.won_at is not None),
        },
        "records": counts,
    }
    (out / MANIFEST).write_text(json.dumps(data, indent=1) + "\n")
    return Manifest(seed, calendar.today, days, data)
