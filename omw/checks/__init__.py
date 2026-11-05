"""Post-sync checks (DESIGN.md §7), written to ``ops.check_results``; ``ops.freshness`` is the agent's view.

- **over-count**: a breakdown grain may not exceed its ``core`` grain by more than 1% on any group.
- **freshness**: every source's last successful sync is within its budget; a dead token or a silent
  empty pull fails here.
- **row-count sanity**: the current window against the previous one, flagging collapses and explosions.

``omw check`` runs them all and exits non-zero on any failure, for the scheduler.
"""

from omw.checks.freshness import DEFAULT_BUDGET_HOURS, freshness_checks
from omw.checks.overcount import OVERCOUNT_RULES, OvercountRule, overcount_checks
from omw.checks.results import CheckResult, Status, write_results
from omw.checks.rowcount import ROWCOUNT_RULES, RowcountRule, rowcount_checks
from omw.checks.runner import CheckRun, run_checks

__all__ = [
    "DEFAULT_BUDGET_HOURS",
    "OVERCOUNT_RULES",
    "ROWCOUNT_RULES",
    "CheckResult",
    "CheckRun",
    "OvercountRule",
    "RowcountRule",
    "Status",
    "freshness_checks",
    "overcount_checks",
    "rowcount_checks",
    "run_checks",
    "write_results",
]
