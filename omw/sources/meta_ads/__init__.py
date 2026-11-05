"""Meta Ads source (docs/THREADS.md T3): entities and insights through the Marketing API, paced.

Entities (``campaigns``, ``ad_sets``, ``ads``) merge on ``id``. Insights are one resource per level
(``insights_campaigns``, ``insights_ads``), one async report job per day, and replace the
``(account_id, date)`` slice they fetched. The HTTP session the SDK uses is wrapped by ``omw.pacing``;
a pacer stop ends every resource cleanly with the complete days it has.

Patterns re-authored from dlt's ``facebook_ads`` verified source (Apache-2.0, see NOTICE).
"""

from omw.sources.meta_ads.cadences import CADENCES, SyncMode, cadence_window
from omw.sources.meta_ads.settings import InsightsLevel
from omw.sources.meta_ads.source import SOURCE_NAME, meta_ads
from omw.sources.meta_ads.token import TokenStatus, check_token

__all__ = [
    "CADENCES",
    "SOURCE_NAME",
    "InsightsLevel",
    "SyncMode",
    "TokenStatus",
    "cadence_window",
    "check_token",
    "meta_ads",
]
