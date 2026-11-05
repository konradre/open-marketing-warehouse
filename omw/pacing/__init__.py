"""Header-aware pacing for the Meta Marketing API (DESIGN.md §5).

``read_usage`` turns Meta's four usage headers into one utilisation number. ``MetaPacer`` wraps a
``requests`` session: it sleeps in proportion to the excess above ``slow_at``, stops the run cleanly at
``stop_at`` or on a rate-limit error (code 4 and its relatives) instead of retrying, and keeps the record the
pipeline writes to ``ops``.
"""

from omw.pacing.headers import USAGE_HEADERS, Reading, Usage, read_usage
from omw.pacing.pacer import (
    RATE_LIMIT_ERROR_CODES,
    Decision,
    MetaPacer,
    PacerConfig,
    PacerStop,
    StopReason,
)

__all__ = [
    "RATE_LIMIT_ERROR_CODES",
    "USAGE_HEADERS",
    "Decision",
    "MetaPacer",
    "PacerConfig",
    "PacerStop",
    "Reading",
    "StopReason",
    "Usage",
    "read_usage",
]
