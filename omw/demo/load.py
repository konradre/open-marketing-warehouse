"""Run the seven real pipelines against one fixture snapshot.

Each loader calls the pipeline's own entry point (``run_daily``, ``run_backfill``, ``run``) with the same
arguments a scheduler would, plus the fixture session. Nothing else is swapped: the dlt source,
the merge that replaces a re-fetched slice, the marts SQL and the campaign key sync all run as they do
against the platforms.

A warehouse is loaded twice. The first load takes the whole history from the ``initial`` snapshot (Search
Console through its chunked backfill, the others through one wide window). The second is an ordinary daily
sync against ``restated``: each source re-fetches its own rolling window and replaces it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from omw.demo.fixtures import SOURCES, Manifest
from omw.demo.replay import TOKEN, Replay, token_provider
from omw.demo.world import Snapshot
from omw.pacing import MetaPacer
from omw.pacing.pacer import PacerSummary
from omw.pipelines import ga4, google_ads, linkedin_ads, meta_ads, search_console, stripe, twenty
from omw.sources.ga4 import RE_PULL_DAYS as GA4_DAYS
from omw.sources.ga4 import GA4Client
from omw.sources.google_ads.client import GoogleAdsClient
from omw.sources.linkedin_ads import LINKEDIN_VERSION, LinkedInAuth
from omw.sources.linkedin_ads import RE_PULL_DAYS as LINKEDIN_DAYS
from omw.sources.meta_ads.cadences import SyncMode
from omw.sources.meta_ads.settings import BREAKDOWNS
from omw.sources.search_console import SearchConsoleClient
from omw.sources.stripe import StripeClient
from omw.sources.windows import Window

log = logging.getLogger(__name__)

WON_STAGES = ("CUSTOMER",)
LOST_STAGES = ("LOST",)
"""The synthetic CRM has a closed-lost stage, so the demo sets ``sources.twenty.lost_stages`` too."""
FIXTURE_KEY = "fixture-key"


@dataclass
class Run:
    """One load of one snapshot."""

    manifest: Manifest
    snapshot: Snapshot
    replay: Replay
    destination: Any
    pipelines_dir: str
    first: bool
    """The first load of a warehouse takes the whole history; a later one the pipelines' daily windows."""
    pacing: PacerSummary | None = None
    """What the Meta pacer saw during this run."""
    meta_mode: SyncMode | None = None
    """The Meta cadence to run instead of the one ``first`` implies: the scheduler in fixture mode runs the
    28-day ``weekly`` reconcile over its own window, on a warehouse that is already loaded."""

    @property
    def today(self) -> date:
        """The day the pipelines believe it is: the day the snapshot was taken."""
        return self.manifest.calendar.as_of(self.snapshot)

    @property
    def history(self) -> Window:
        return Window(self.manifest.calendar.start, self.today - timedelta(days=1))

    def accounts(self, source: str) -> dict[str, Any]:
        found: dict[str, Any] = self.manifest.data["accounts"][source]
        return found

    def pipeline(self, module: Any) -> Any:
        return module.build_pipeline(self.pipelines_dir, self.destination)


def load_search_console(run: Run) -> dict[str, int]:
    """The 16-month backfill in 90-day chunks on the first load (the chunks before the company existed
    come back empty), then the daily sync of the rolling 10 days."""
    client = SearchConsoleClient(token_provider, session=run.replay.session, row_limit=5000)
    sites = run.accounts("search_console")["site_urls"]
    pipeline = run.pipeline(search_console)
    if run.first:
        backfill = search_console.run_backfill(pipeline, sites, today=run.today, client=client)
        if not backfill.ok:
            raise RuntimeError(f"search_console backfill failed at {backfill.failed}") from backfill.error
        log.info("search_console: backfill loaded %d chunks", len(backfill.completed))
    return search_console.run_daily(pipeline, today=run.today, site_urls=sites, client=client)


def load_meta_ads(run: Run) -> dict[str, int]:
    """Campaign and ad level, the three breakdowns and the lead forms, through the SDK and the pacer; a
    28-day-style reconcile over the whole history first, the 7-day daily re-pull afterwards."""
    window: dict[str, Any] = {"window": run.history} if run.first else {}
    pacer = MetaPacer(sleep=_no_wait)
    result = meta_ads.run(
        run.pipeline(meta_ads),
        run.meta_mode or ("weekly" if run.first else "daily"),
        run.accounts("meta_ads")["account_ids"],
        levels=("campaign", "ad"),
        today=run.today,
        breakdowns=tuple(BREAKDOWNS),
        leads=True,
        pacer=pacer,
        access_token=TOKEN,
        session=run.replay.new_session(),
        sleep=_no_wait,
        **window,
    )
    if result.stopped is not None:
        raise RuntimeError(f"the pacer ended the Meta run early: {result.stopped.detail}")
    log.info(
        "meta_ads: %d Graph API requests, peak utilisation %.0f%% (%s), %d pacer sleeps",
        pacer.summary.requests,
        pacer.summary.peak_utilisation,
        pacer.summary.tier,
        pacer.summary.sleeps,
    )
    run.pacing = pacer.summary
    return result.counts


def _no_wait(_seconds: float) -> None:
    """Async report runs complete at once in fixture mode: no need to wait between polls."""


def load_google_ads(run: Run) -> dict[str, int]:
    accounts = run.accounts("google_ads")
    client = GoogleAdsClient(
        token_provider, "fixture-developer-token", accounts["login_customer_id"], session=run.replay.session
    )
    window: dict[str, Any] = {"window": run.history} if run.first else {}
    return google_ads.run_daily(
        run.pipeline(google_ads),
        today=run.today,
        customer_ids=accounts["customer_ids"],
        client=client,
        **window,
    )


def load_ga4(run: Run) -> dict[str, int]:
    client = GA4Client(token_provider, session=run.replay.session, limit=1000)
    days = run.history.days if run.first else GA4_DAYS
    return ga4.run_daily(
        run.pipeline(ga4),
        today=run.today,
        days=days,
        property_ids=run.accounts("ga4")["property_ids"],
        client=client,
    )


def load_stripe(run: Run) -> dict[str, int]:
    client = StripeClient(FIXTURE_KEY, session=run.replay.session)
    window: dict[str, Any] = {"invoice_window_days": run.history.days + 1} if run.first else {}
    return stripe.run_daily(run.pipeline(stripe), today=run.today, client=client, **window)


def load_linkedin_ads(run: Run) -> dict[str, int]:
    session = run.replay.session
    auth = LinkedInAuth(LINKEDIN_VERSION, token_provider=token_provider)
    session.auth = auth
    days = run.history.days if run.first else LINKEDIN_DAYS
    return linkedin_ads.run_daily(
        run.pipeline(linkedin_ads),
        today=run.today,
        days=days,
        leads=True,
        account_ids=run.accounts("linkedin_ads")["account_ids"],
        auth=auth,
        session=session,
    )


def load_twenty(run: Run) -> dict[str, int]:
    return twenty.run_daily(
        run.pipeline(twenty),
        won=WON_STAGES,
        lost=LOST_STAGES,
        base_url=run.accounts("twenty")["base_url"],
        api_key=FIXTURE_KEY,
        session=run.replay.session,
    )


LOADERS: dict[str, Callable[[Run], dict[str, int]]] = {
    "search_console": load_search_console,
    "meta_ads": load_meta_ads,
    "google_ads": load_google_ads,
    "ga4": load_ga4,
    "stripe": load_stripe,
    "linkedin_ads": load_linkedin_ads,
    "twenty": load_twenty,
}
assert tuple(LOADERS) == SOURCES


def load_snapshot(run: Run, sources: Sequence[str] = SOURCES) -> dict[str, dict[str, int]]:
    """Load ``sources`` from the run's snapshot. Returns rows normalised per table in each source's last
    pipeline run."""
    loaded: dict[str, dict[str, int]] = {}
    for source in sources:
        counts = LOADERS[source](run)
        rows = sum(count for table, count in counts.items() if not table.startswith("_dlt"))
        log.info(
            "%s: %s sync from %s loaded %d rows",
            source,
            "first" if run.first else "daily",
            run.snapshot,
            rows,
        )
        loaded[source] = counts
    return loaded
