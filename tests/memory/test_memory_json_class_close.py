"""Closes the JSON poison-fetch class in the last memory stores.

`KnowledgeEntry.from_dict` and `Routine` are both built inside list comprehensions
over `fetchall()`, so a bare `json.loads` raising on ONE corrupt blob
lost the ENTIRE list — the knowledge base, the routine list — rather than degrading
the single bad record.

One thing is pinned here that a naive sweep would get wrong: `KnowledgeEntry.from_dict`
must keep passing an ALREADY-parsed value through untouched — `import_entries` feeds it
plain dicts from a JSON import, not DB rows, so degrading those to the default would
silently drop the imported data.
"""

from __future__ import annotations

from navig.memory.knowledge_base import KnowledgeEntry

# ── KnowledgeEntry.from_dict ─────────────────────────────────────────────────


def _entry_row(**over):
    row = {
        "id": "e1",
        "key": "k",
        "content": "c",
        "summary": None,
        "tags": '["a"]',
        "source": "",
        "created_at": "2026-01-01T00:00:00",
        "expires_at": None,
        "metadata": "{}",
        "embedding": None,
    }
    row.update(over)
    return row


def test_knowledge_entry_degrades_a_corrupt_blob_instead_of_raising():
    entry = KnowledgeEntry.from_dict(
        _entry_row(tags="{not json", metadata="{also bad", embedding="{bad")
    )
    assert entry.tags == []
    assert entry.metadata == {}
    assert entry.embedding is None
    assert entry.key == "k"  # the real content is untouched


def test_knowledge_entry_still_passes_already_parsed_values_through():
    """import_entries hands from_dict plain dicts, not DB strings — a list/dict there
    must survive verbatim (degrading it to the default would silently drop the import)."""
    entry = KnowledgeEntry.from_dict(_entry_row(tags=["x", "y"], metadata={"m": 1}))
    assert entry.tags == ["x", "y"]
    assert entry.metadata == {"m": 1}


def test_knowledge_entry_valid_json_unchanged():
    entry = KnowledgeEntry.from_dict(_entry_row(tags='["a","b"]', metadata='{"m":1}'))
    assert entry.tags == ["a", "b"]
    assert entry.metadata == {"m": 1}


# ── Routine (knowledge_graph) ────────────────────────────────────────────────


def test_routine_degrades_a_corrupt_task_spec():
    """`[Routine(dict(r)) for r in rows]` — one corrupt task_spec must not blank the
    whole routine list."""
    from navig.memory.knowledge_graph import Routine

    r = Routine({"id": "r1", "name": "n", "schedule": "daily", "created_at": "2026-01-01T00:00:00", "task_spec": "{not json"})
    assert r.task_spec is None
    assert r.name == "n"  # the rest of the row is intact


def test_routine_valid_task_spec_unchanged():
    from navig.memory.knowledge_graph import Routine

    r = Routine({"id": "r1", "name": "n", "schedule": "daily", "created_at": "2026-01-01T00:00:00", "task_spec": '{"a": 1}'})
    assert r.task_spec == {"a": 1}
    # absent / NULL stays None, exactly as before
    assert Routine({"id": "r", "name": "n", "schedule": "d", "created_at": "2026-01-01T00:00:00"}).task_spec is None
