"""A single corrupt JSON column must never poison a whole memory fetch.

The memory stores parse JSON columns (tags / embedding / metadata / cached values)
inside fetch loops — ``[Row.from_row(r) for r in rows]``. A bare ``json.loads``
raising on ONE corrupt or NULL row there loses the ENTIRE fetch (every row), not the
one bad blob — the same class fixed for ConversationStore (#e6d056e2). These tests
pin the shared ``safe_json_loads`` degrade for key_facts + storage.
"""

from __future__ import annotations

import pytest

from navig.memory._util import safe_json_loads

pytestmark = pytest.mark.integration


def test_safe_json_loads_degrades_to_default():
    assert safe_json_loads('{"a": 1}', {}) == {"a": 1}
    assert safe_json_loads("[1, 2]", []) == [1, 2]
    assert safe_json_loads(None, []) == []          # NULL column
    assert safe_json_loads("", {}) == {}            # empty string
    assert safe_json_loads("{ not json", {}) == {}  # malformed → default
    assert safe_json_loads("nope", None) is None
    # the default's identity is preserved (no shared-mutable surprise)
    d1: list = []
    assert safe_json_loads(None, d1) is d1


def test_keyfact_from_row_tolerates_corrupt_blobs():
    """A KeyFact row with malformed tags/embedding/metadata degrades to the field
    default instead of raising — so list_facts/search (list comps over from_row) can't
    be poisoned by one bad row."""
    from navig.memory.key_facts import KeyFact

    row = {
        "id": "k1",
        "content": "the user prefers dark mode",
        "category": "preference",
        "tags": "{ corrupt",          # malformed JSON
        "confidence": 0.9,
        "source_conversation_id": None,
        "source_platform": None,
        "created_at": "2026-07-27T00:00:00",
        "updated_at": "2026-07-27T00:00:00",
        "superseded_by": None,
        "deleted": 0,
        "access_count": 0,
        "last_accessed": None,
        "embedding": "not-a-vector",   # malformed JSON
        "metadata": None,              # NULL
        "approved": 1,
    }
    fact = KeyFact.from_row(row)  # pre-fix: json.loads('{ corrupt') → raises
    assert fact.content == "the user prefers dark mode"
    assert fact.tags == []            # degraded, not raised
    assert fact.embedding is None
    assert fact.metadata == {}


def test_memorychunk_from_dict_tolerates_corrupt_blobs():
    """MemoryChunk.from_dict (used in list comps over fetchall) degrades a corrupt
    embedding/metadata blob rather than poisoning the whole search result."""
    from navig.memory.storage import MemoryChunk

    data = {
        "id": "c1",
        "file_path": "a.py",
        "content": "def f(): ...",
        "line_start": 1,
        "line_end": 1,
        "token_count": 4,
        "embedding": "{bad",       # malformed
        "metadata": "also bad {",  # malformed string
        "created_at": "2026-07-27T00:00:00",
    }
    chunk = MemoryChunk.from_dict(data)  # pre-fix: json.loads('{bad') → raises
    assert chunk.content == "def f(): ..."
    assert chunk.embedding is None
    assert chunk.metadata == {}
