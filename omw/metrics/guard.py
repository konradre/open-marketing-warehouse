"""The second guard (DESIGN.md §6): exactly one SELECT or WITH statement, nothing else, before anything
runs as the agent user. The server-side profile remains the real limit."""

from __future__ import annotations

import re

FORBIDDEN = (
    "insert",
    "alter",
    "drop",
    "create",
    "attach",
    "detach",
    "truncate",
    "rename",
    "optimize",
    "set",
    "grant",
    "revoke",
    "kill",
    "exchange",
    "into outfile",
    "settings",
)
_STRING = re.compile(r"'(?:[^'\\]|\\.)*'")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


class SqlGuardError(ValueError):
    pass


def _bare(sql: str) -> str:
    """The statement with string literals and comments blanked, for keyword checks."""
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    return _STRING.sub("''", sql)


def assert_single_select(sql: str) -> str:
    """Return ``sql`` stripped, or raise ``SqlGuardError``."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise SqlGuardError("empty statement")
    bare = _bare(stripped)
    if ";" in bare:
        raise SqlGuardError("more than one statement")
    first = bare.lstrip().split(None, 1)[0].lower()
    if first not in ("select", "with"):
        raise SqlGuardError(f"not a SELECT: starts with {first!r}")
    lowered = f" {re.sub(r'\s+', ' ', bare.lower())} "
    for word in FORBIDDEN:
        if re.search(rf"(?<![a-z_]){re.escape(word)}(?![a-z_])", lowered):
            raise SqlGuardError(f"forbidden keyword {word!r}")
    return stripped
