"""FTS hygiene: don't strand index rows, don't hard-fail on a damaged index.

Two gaps found while sweeping every FTS5 table in the tree after the links_db
corruption fix (#530c0a21):

* `key_facts_fts` is a STANDALONE fts table synced by hand — there is no delete
  trigger — so `purge_deleted()`'s hard delete left the purged facts' text in the
  index forever: unbounded growth plus BM25 ranking skewed by documents that no
  longer exist. (`search_keyword` INNER JOINs `key_facts`, so orphans were never
  returned as results — this is hygiene, not a correctness leak.)
* `search_keyword` / `search_facts` caught only `sqlite3.OperationalError`, but a
  damaged index raises its PARENT `sqlite3.DatabaseError` — so the LIKE fallback
  never fired on exactly the databases that needed it.
"""

from __future__ import annotations

import sqlite3

# ── purge_deleted must not strand FTS rows ───────────────────────────────────


def _fts_ids(store) -> set[str]:
    return {
        r[0] for r in store._conn().execute("SELECT fact_id FROM key_facts_fts").fetchall()
    }


def _make_store(tmp_path):
    from navig.memory.key_facts import KeyFactStore

    return KeyFactStore(db_path=tmp_path / "key_facts.db")


def _remember(store, content: str) -> str:
    from navig.memory.key_facts import KeyFact

    return store.upsert(KeyFact(content=content)).id


def test_purge_deleted_also_drops_the_fts_rows(tmp_path):
    store = _make_store(tmp_path)
    kept = _remember(store, "alpha fact stays")
    doomed = _remember(store, "beta fact goes away")

    assert _fts_ids(store) >= {kept, doomed}

    store.soft_delete(doomed)
    purged = store.purge_deleted(older_than_days=-1)  # negative window → purge now
    assert purged == 1

    remaining = _fts_ids(store)
    assert doomed not in remaining, "the purged fact's index row must be gone"
    assert kept in remaining, "the surviving fact must stay indexed"


def test_purge_deleted_keeps_search_working(tmp_path):
    store = _make_store(tmp_path)
    _remember(store, "alpha stays searchable")
    doomed = _remember(store, "beta disappears")
    store.soft_delete(doomed)
    store.purge_deleted(older_than_days=-1)

    hits = [f.content for f, _ in store.search_keyword("alpha")]
    assert any("alpha" in h for h in hits)
    assert store.search_keyword("beta") == []


def test_purge_with_nothing_to_purge_is_a_noop(tmp_path):
    store = _make_store(tmp_path)
    kept = _remember(store, "nothing to purge here")
    assert store.purge_deleted(older_than_days=-1) == 0
    assert kept in _fts_ids(store)


# ── the search fallbacks must catch DatabaseError, not just OperationalError ──


class _MalformedOnFTS:
    """Raises the real corruption error on the FTS query, passes everything else."""

    def __init__(self, real, marker: str):
        self._real = real
        self._marker = marker
        self._raised = False

    def execute(self, sql, *args, **kw):
        if self._marker in str(sql) and not self._raised:
            self._raised = True
            raise sqlite3.DatabaseError("database disk image is malformed")
        return self._real.execute(sql, *args, **kw)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_key_facts_search_falls_back_on_a_damaged_index(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    _remember(store, "findme in the key facts")

    real = store._conn()
    monkeypatch.setattr(store, "_conn", lambda: _MalformedOnFTS(real, "key_facts_fts"))

    hits = store.search_keyword("findme")  # must degrade to LIKE, not raise

    assert [f.content for f, _ in hits] == ["findme in the key facts"]


def test_knowledge_graph_search_falls_back_on_a_damaged_index(tmp_path):
    from navig.memory.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph(tmp_path / "kg.db")
    try:
        kg.remember_fact("alice", "lives_in", "Berlin", overwrite=True)

        real = kg._con
        kg._con = _MalformedOnFTS(real, "facts_fts")  # type: ignore[assignment]
        facts = kg.search_facts("Berlin")  # must degrade to LIKE, not raise
        kg._con = real  # type: ignore[assignment]

        assert [f.object for f in facts] == ["Berlin"]
    finally:
        kg.close()
