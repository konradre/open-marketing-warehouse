"""The metric layer (DESIGN.md §6): metric definitions, the SQL compiler and the ``query_metrics`` MCP server.

``schema`` loads and validates ``metrics/*.yaml``; ``compiler`` turns a ``MetricRequest`` into one
ClickHouse ``SELECT`` (one CTE per source for cross-source measures, joined on the common grain);
``moods`` decides whether an answer is served, disclosed, clarified or refused; ``guard`` asserts a single
``SELECT``/``WITH`` statement before anything runs as the ``agent`` user; ``server`` exposes
``list_metrics``, ``list_dimensions`` and ``query_metrics`` over MCP.
"""

from omw.metrics.compiler import Plan, compile_request
from omw.metrics.guard import SqlGuardError, assert_single_select
from omw.metrics.moods import Answer, Mood
from omw.metrics.request import RELATIVE_RANGES, Compare, MetricRequest, Relative, TimeRange, relative_range
from omw.metrics.schema import Catalog, DerivedMetric, Metric, Report, load_catalog

__all__ = [
    "Answer",
    "Catalog",
    "Compare",
    "DerivedMetric",
    "Metric",
    "MetricRequest",
    "Mood",
    "Plan",
    "RELATIVE_RANGES",
    "Relative",
    "Report",
    "SqlGuardError",
    "TimeRange",
    "assert_single_select",
    "compile_request",
    "load_catalog",
    "relative_range",
]
