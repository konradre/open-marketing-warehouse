"""The second guard (DESIGN.md §6): exactly one SELECT or WITH statement, nothing else, before anything
runs as the agent user. The server-side profile remains the real limit.

The statement is read the way ClickHouse's lexer reads it, one character at a time: a string literal, a
quoted identifier, a heredoc and a comment each end only where ClickHouse ends them, so a quote inside a
comment or a comment marker inside a string cannot hide what follows. Whatever the lexer here cannot place
(an unterminated quote or comment, a stray ``$`` or ``#``) is refused, never guessed at.
"""

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
_HEREDOC_TAG = re.compile(r"\$[A-Za-z0-9_]*\$")
_QUOTES = {"'": "''", '"': '""', "`": "``"}
"""Opening quote -> what the quoted text is replaced with: an empty literal or identifier of the same kind."""


class SqlGuardError(ValueError):
    pass


def _quoted_end(sql: str, start: int) -> int:
    """The index after the closing quote of the literal or identifier opening at ``start``. Inside, a
    backslash escapes the next character and a doubled quote is the quote itself, as in ClickHouse."""
    quote = sql[start]
    i = start + 1
    while i < len(sql):
        char = sql[i]
        if char == "\\":
            i += 2
            continue
        if char == quote:
            if sql.startswith(quote, i + 1):
                i += 2
                continue
            return i + 1
        i += 1
    raise SqlGuardError(f"unterminated {quote} quote")


def _bare(sql: str) -> str:
    """The statement with string literals, quoted identifiers, heredocs and comments blanked, for the
    keyword checks."""
    out: list[str] = []
    i = 0
    while i < len(sql):
        char = sql[i]
        if char in _QUOTES:
            i = _quoted_end(sql, i)
            out.append(_QUOTES[char])
        elif sql.startswith("--", i) or (char == "#" and sql[i + 1 : i + 2] in (" ", "!")):
            end = sql.find("\n", i)
            i = len(sql) if end == -1 else end
            out.append(" ")
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end == -1:
                raise SqlGuardError("unterminated /* comment")
            i = end + 2
            out.append(" ")
        elif char == "$":
            tag = _HEREDOC_TAG.match(sql, i)
            end = sql.find(tag.group(), tag.end()) if tag else -1
            if tag is None or end == -1:
                raise SqlGuardError("a $ outside a string that is not a complete heredoc")
            i = end + len(tag.group())
            out.append("''")
        elif char == "#":
            raise SqlGuardError("a # outside a string that does not start a comment")
        else:
            out.append(char)
            i += 1
    return "".join(out)


def assert_single_select(sql: str) -> str:
    """Return ``sql`` stripped, or raise ``SqlGuardError``."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise SqlGuardError("empty statement")
    bare = _bare(stripped)
    if ";" in bare:
        raise SqlGuardError("more than one statement")
    words = bare.split(None, 1)
    first = words[0].lower() if words else ""
    if first not in ("select", "with"):
        raise SqlGuardError(f"not a SELECT: starts with {first!r}")
    lowered = f" {re.sub(r'\s+', ' ', bare.lower())} "
    for word in FORBIDDEN:
        if re.search(rf"(?<![a-z_]){re.escape(word)}(?![a-z_])", lowered):
            raise SqlGuardError(f"forbidden keyword {word!r}")
    return stripped
