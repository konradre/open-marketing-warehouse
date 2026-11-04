"""Apply a ``sql/*.sql`` file of ``marts`` view definitions through a dlt pipeline's SQL client.

Views over ``raw`` can only be created once dlt has made the tables, so each pipeline applies its own
``sql/<NN>_<source>_marts.sql`` after a successful load. Statements are separated by ``;`` at line end and
must be idempotent (``CREATE OR REPLACE VIEW``).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import dlt

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
_STATEMENT_END = re.compile(r";\s*$", re.MULTILINE)


def statements(sql_text: str) -> list[str]:
    """Statements of a file, comment-only lines dropped, empty statements skipped."""
    found: list[str] = []
    for chunk in _STATEMENT_END.split(sql_text):
        lines = [line for line in chunk.splitlines() if not line.strip().startswith("--")]
        statement = "\n".join(lines).strip()
        if statement:
            found.append(statement)
    return found


def apply_marts(pipeline: dlt.Pipeline, sql_file: Path) -> int:
    """Run every statement of ``sql_file`` as the pipeline's warehouse user. Returns the count."""
    stmts = statements(sql_file.read_text())
    with pipeline.sql_client() as client:
        for statement in stmts:
            client.execute_sql(statement)
    log.info("applied %s statements from %s", len(stmts), sql_file.name)
    return len(stmts)
