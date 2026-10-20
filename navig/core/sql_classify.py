"""Classify SQL by what it can change — the one classifier behind every "run SQL" surface.

Used by ``navig db query`` (via ``console_helper.classify_sql``) and the deck's
``POST /api/deck/db/query``. Levels, in increasing danger:

* ``verbose``  — proven read-only (``SELECT``, ``SHOW``, ``EXPLAIN`` without ``ANALYZE``, …)
* ``standard`` — writes or changes something (``INSERT``, ``UPDATE``, DDL, grants, and
  ANY statement this module does not recognise)
* ``critical`` — removes data (``DROP``, ``TRUNCATE``, ``DELETE``)

The previous classifier matched substrings of the raw text, which failed both ways:
``SELECT * FROM deleted_items`` was "critical", and anything it had no word for
(``CALL``, ``COPY``, ``SET``, ``MERGE``, a data-modifying ``WITH``) was "read-only".
This one reads statement keywords with comments and quoted text removed, and fails
CLOSED: what is not proven read-only is a write.

It is a guard against accidents, not a sandbox. A ``SELECT`` that calls a function
with side effects (``pg_terminate_backend``, ``setval``) still reads as ``verbose``;
only a database user without write grants makes a connection truly read-only.
"""

from __future__ import annotations

import re

_LEVELS = ("verbose", "standard", "critical")

_CRITICAL = frozenset({"DROP", "TRUNCATE", "DELETE"})
_READ = frozenset({"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "VALUES", "TABLE", "HELP"})
# Transaction control changes nothing by itself; what it wraps is classified separately.
_NEUTRAL = frozenset({"BEGIN", "START", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE", "USE"})
_CTE_WRITES = {
    "DELETE": "critical",
    "INSERT": "standard",
    "UPDATE": "standard",
    "MERGE": "standard",
}

# Comments, quoted strings / identifiers and Postgres dollar-quoted bodies, in one pass so
# a quote inside a comment (or a comment marker inside a string) cannot confuse the scan.
_STRIP = re.compile(
    r"--[^\n]*"
    r"|/\*.*?(?:\*/|$)"
    r"|'(?:[^'\\]|\\.|'')*(?:'|$)"
    r'|"(?:[^"\\]|\\.|"")*(?:"|$)'
    r"|`(?:[^`]|``)*(?:`|$)"
    r"|\$([A-Za-z_]\w*)?\$.*?(?:\$\1\$|$)",
    re.DOTALL,
)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _statements(query: str) -> list[tuple[str, list[str]]]:
    """``(text, UPPERCASE words)`` per statement, with comments and quoted text blanked."""
    bare = _STRIP.sub(" ", query or "")
    out: list[tuple[str, list[str]]] = []
    for text in bare.split(";"):
        words = [w.upper() for w in _WORD.findall(text)]
        if words:
            out.append((text, words))
    return out


def _level_of(text: str, words: list[str]) -> str:
    first = words[0]
    if first in _CRITICAL:
        return "critical"
    if first in _NEUTRAL:
        return "verbose"
    if first == "EXPLAIN":
        # EXPLAIN ANALYZE executes the statement it explains (Postgres): judge that one.
        if "ANALYZE" in words or "ANALYSE" in words:
            for i, w in enumerate(words[1:], start=1):
                if w in _CRITICAL or w in _CTE_WRITES or (w in _READ and w != "EXPLAIN"):
                    return _level_of(text, words[i:])
            return "standard"
        return "verbose"
    if first == "WITH":
        # A data-modifying CTE (`WITH x AS (DELETE … RETURNING …) SELECT …`) writes.
        found = [_CTE_WRITES[w] for w in words if w in _CTE_WRITES]
        return max(found, key=_LEVELS.index) if found else "verbose"
    if first == "SELECT":
        # SELECT … INTO creates a table (Postgres) or writes a file / variables (MySQL).
        return "standard" if "INTO" in words else "verbose"
    if first == "PRAGMA":
        return "standard" if "=" in text else "verbose"
    if first in _READ:
        return "verbose"
    return "standard"


def classify_sql(query: str) -> str:
    """The most dangerous level among the statements in *query* (empty → ``verbose``)."""
    level = "verbose"
    for text, words in _statements(query):
        found = _level_of(text, words)
        if _LEVELS.index(found) > _LEVELS.index(level):
            level = found
    return level


def statement_keywords(query: str) -> list[str]:
    """The leading keyword of each statement (``["SELECT", "DELETE"]``): safe to log, no data."""
    return [words[0] for _text, words in _statements(query)]
