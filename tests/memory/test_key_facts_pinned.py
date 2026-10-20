"""Pinned memories: an additive v2 column, always injected, listed first."""

from __future__ import annotations

import sqlite3

from navig.memory.fact_retriever import FactRetriever
from navig.memory.key_facts import KeyFact, KeyFactStore


def test_v1_database_gains_the_pinned_column_and_records_the_version(tmp_path):
    db = tmp_path / "kf.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE key_facts (
            id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'context',
            tags TEXT DEFAULT '[]', confidence REAL DEFAULT 0.8,
            source_conversation_id TEXT DEFAULT '', source_platform TEXT DEFAULT 'core',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, superseded_by TEXT,
            deleted INTEGER DEFAULT 0, access_count INTEGER DEFAULT 0, last_accessed TEXT,
            embedding TEXT, metadata TEXT DEFAULT '{}', approved INTEGER DEFAULT 1);
        CREATE TABLE kf_meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO kf_meta VALUES ('schema_version', '1');
        INSERT INTO key_facts (id, content, created_at, updated_at)
            VALUES ('old', 'Prefers dark themes', '2026-01-01', '2026-01-01');
        """
    )
    conn.commit()
    conn.close()

    s = KeyFactStore(db_path=db)
    old = s.get("old")
    assert old is not None and old.pinned is False and old.content == "Prefers dark themes"
    assert s.get_meta("schema_version") == str(KeyFactStore.SCHEMA_VERSION)
    KeyFactStore(db_path=db)  # idempotent re-open
    assert s.set_pinned("old", True) is True


def test_set_pinned_round_trips_and_misses_honestly(tmp_path):
    s = KeyFactStore(db_path=tmp_path / "kf.db")
    f = s.upsert(KeyFact(content="Works in UTC+1"))
    assert s.set_pinned(f.id, True) is True
    assert s.get(f.id).pinned is True  # type: ignore[union-attr]
    assert s.set_pinned("nope", True) is False
    s.soft_delete(f.id)
    assert s.set_pinned(f.id, False) is False  # deleted facts can't be (un)pinned


def test_list_saved_puts_pinned_first_hides_pending_and_filters(tmp_path):
    s = KeyFactStore(db_path=tmp_path / "kf.db")
    a = s.upsert(KeyFact(content="Uses Neovim for editing"))
    b = s.upsert(KeyFact(content="Has a cat called Miso"))
    s.upsert(KeyFact(content="Maybe likes jazz", approved=None))
    s.set_pinned(a.id, True)
    facts, total = s.list_saved()
    assert total == 2 and [f.id for f in facts] == [a.id, b.id]
    facts, total = s.list_saved(query="miso")
    assert total == 1 and facts[0].id == b.id
    assert s.list_saved(query="100%_")[1] == 0  # LIKE wildcards are literal


def test_pinned_fact_is_recalled_for_an_unrelated_query(tmp_path):
    s = KeyFactStore(db_path=tmp_path / "kf.db")
    f = s.upsert(KeyFact(content="Always answer in British English", category="preference"))
    s.upsert(KeyFact(content="Deploys to Hetzner", category="technical"))
    result = FactRetriever(s).retrieve("how do I bake bread")
    assert all(rf.fact.id != f.id for rf in result.facts)  # control: not relevant
    s.set_pinned(f.id, True)
    result = FactRetriever(s).retrieve("how do I bake bread")
    assert result.facts and result.facts[0].fact.id == f.id


def test_pending_pinned_fact_is_never_injected(tmp_path):
    s = KeyFactStore(db_path=tmp_path / "kf.db")
    f = s.upsert(KeyFact(content="Prefers tabs over spaces", approved=None))
    s.set_pinned(f.id, True)
    assert FactRetriever(s).retrieve("anything").facts == []
