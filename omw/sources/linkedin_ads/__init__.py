"""LinkedIn Ads source (docs/THREADS.md T8): dlt ``rest_api`` resources over the versioned Marketing API.

Entities (``ad_accounts``, ``campaign_groups`` with their budgets and budget-optimisation fields,
``campaigns``, ``creatives``) merge on ``id``; ``ad_analytics`` is daily by campaign and replaces the
``(account_id, date)`` slice it fetched. The ``LinkedIn-Version`` header is pinned in one constant
(``LINKEDIN_VERSION``): the pipeline warns within three months of the end of LinkedIn's 12-month support
window and refuses to run past it. Auth is the OAuth refresh-token flow from secrets.

Re-authored from the patterns in cesteral's mcp-open-advertising (Apache-2.0, see NOTICE).
"""

from omw.sources.linkedin_ads.auth import LinkedInAuth, refresh_access_token
from omw.sources.linkedin_ads.source import RE_PULL_DAYS, SOURCE_NAME, linkedin_ads
from omw.sources.linkedin_ads.version import LINKEDIN_VERSION, VersionStatus, check_version

__all__ = [
    "LINKEDIN_VERSION",
    "RE_PULL_DAYS",
    "SOURCE_NAME",
    "LinkedInAuth",
    "VersionStatus",
    "check_version",
    "linkedin_ads",
    "refresh_access_token",
]
