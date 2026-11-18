"""Fixture mode: everything the real pipelines need to run against fixture files instead of the platforms.

``Replay`` wires one snapshot directory to the clients: a session whose transport answers from the files
(``transport``, with the per-API handlers in ``google`` and ``rest``) and fixture account objects for Meta
(``meta``). ``omw.demo.load`` hands them to the pipelines' own entry points.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from omw.demo.replay import google, rest
from omw.demo.replay.meta import FixtureAccount, fixture_lead_fetcher
from omw.demo.replay.transport import TOKEN, FixtureAdapter, Fixtures, fixture_session, token_provider

__all__ = [
    "TOKEN",
    "FixtureAccount",
    "FixtureAdapter",
    "Fixtures",
    "Replay",
    "fixture_lead_fetcher",
    "token_provider",
]


@dataclass
class Replay:
    """One snapshot directory, ready to be read through the real clients.

    ``accounts`` is the manifest's ``accounts`` section: the synthetic ids the fixtures were generated for.
    A request for any other account, property or site is refused, as the platform would.
    """

    directory: Path
    accounts: dict[str, Any]
    fixtures: Fixtures = field(init=False)
    adapter: FixtureAdapter = field(init=False)
    session: requests.Session = field(init=False)

    def __post_init__(self) -> None:
        self.fixtures = Fixtures(self.directory)
        accounts = self.accounts
        self.adapter = FixtureAdapter(
            {
                google.SEARCH_CONSOLE_HOST: google.search_console(
                    self.fixtures, accounts["search_console"]["site_urls"]
                ),
                google.GA4_HOST: google.ga4(self.fixtures, accounts["ga4"]["property_ids"]),
                google.GOOGLE_ADS_HOST: google.google_ads(
                    self.fixtures, accounts["google_ads"]["customer_ids"]
                ),
                rest.STRIPE_HOST: rest.stripe(self.fixtures),
                rest.LINKEDIN_HOST: rest.linkedin(self.fixtures),
                urlsplit(accounts["twenty"]["base_url"]).netloc: rest.twenty(self.fixtures),
            }
        )
        self.session = fixture_session(self.adapter)

    def meta_accounts(self) -> dict[str, FixtureAccount]:
        return {
            account_id: FixtureAccount(self.fixtures, account_id)
            for account_id in self.accounts["meta_ads"]["account_ids"]
        }
