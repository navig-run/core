"""FTS5 helpers for Recall.

Standalone FTS tables written by direct INSERT/DELETE (external-content tables with sync
triggers corrupted ``links_fts`` before). The tokenizer folds diacritics and has no
stemmer, so English, Russian and French text search the same way.
"""

from __future__ import annotations

import re
import sqlite3

TOKENIZE = "unicode61 remove_diacritics 2"
_WORD = re.compile(r"\w+", re.UNICODE)
_PHRASE = re.compile(r'"([^"]+)"')


def match_query(query: str, *, max_terms: int = 12, any_term: bool = False) -> str | None:
    """User text → a safe FTS5 MATCH expression (AND of prefix terms; quoted text = phrase).
    ``any_term`` joins with OR instead — for similarity lookups, where BM25 does the ranking.

    Every token is re-quoted, so FTS5 operators (``NEAR``, ``-``, ``*``, column filters)
    in the input are inert.
    """
    parts: list[str] = []
    for phrase in _PHRASE.findall(query):
        words = _WORD.findall(phrase)
        if words:
            parts.append('"' + " ".join(words) + '"')
    rest = _PHRASE.sub(" ", query)
    for w in _WORD.findall(rest):
        parts.append(f'"{w}"*')
    parts = parts[:max_terms]
    return (" OR " if any_term else " ").join(parts) if parts else None


def fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp._recall_fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS temp._recall_fts_probe")
        return True
    except sqlite3.OperationalError:
        return False
