"""open-marketing-warehouse: pipelines, warehouse DDL and the agent's read-only path.

Layout (DESIGN.md §8): ``sources`` (one module per platform), ``pacing`` (the Meta header-aware pacer),
``pipelines`` (run entry points and cadences), ``checks`` (post-sync checks) and ``metrics`` (metric
definition schema, compiler and the ``query_metrics`` MCP server).
"""

__version__ = "0.1.0"
