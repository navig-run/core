"""An FTS self-repair must be VISIBLE — it records an incident, so it pushes.

`links_db` and `knowledge_graph` now heal a search index damaged by the old
external-content triggers (#530c0a21, #d02f935e). That heal is exactly the shape
`navig.core.incidents` exists for: survivable, recurring, and otherwise completely
invisible — "a daemon that heals itself at 3am and tells nobody" is how the original
outage hid. Recording it means:

  * `navig doctor` → Config Health lists it (a pull), and
  * inside the daemon the config-incident producer fans it out as a notification (a
    push) — new incident types are covered automatically, it reads DESCRIPTIONS.

The sharp edge these tests pin: a repair on a BRAND-NEW database is initialisation, not
damage. Recording that would fire an alert every time a store is first created, which is
noise that teaches the operator to ignore the row that matters.
"""

from __future__ import annotations

import sqlite3

import pytest

from navig.core import incidents

# The pre-fix schemas, so a genuinely damaged database can be built to heal.
_BROKEN_LINKS_TRIGGERS = """
DROP TRIGGER IF EXISTS links_fts_update;
CREATE TRIGGER links_fts_update AFTER UPDATE ON links BEGIN
    UPDATE links_fts SET url=new.url, title=COALESCE(new.title,''),
        notes=COALESCE(new.notes,''), tags=COALESCE(new.tags,'') WHERE id=new.id;
END;
"""

_BROKEN_KG_TRIGGER = """
DROP TRIGGER IF EXISTS facts_fts_delete;
CREATE TRIGGER facts_fts_delete AFTER DELETE ON facts BEGIN
    DELETE FROM facts_fts WHERE rowid=old.rowid;
END;
"""


@pytest.fixture
def recorded(monkeypatch):
    """Capture incidents instead of writing them to the operator's real log."""
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(incidents, "record", lambda event, **data: seen.append((event, data)))
    return seen


# ── the repair is reported ───────────────────────────────────────────────────


def test_links_repair_records_an_incident(tmp_path, recorded):
    from navig.memory.links_db import LinksDB

    path = tmp_path / "links.db"
    db = LinksDB(path)  # creates it — no incident (see the noise test below)
    db._con.executescript(_BROKEN_LINKS_TRIGGERS)
    db._con.execute("PRAGMA user_version = 0")
    db._con.commit()
    db.add("https://a.example", title="Alpha")
    db.close()
    recorded.clear()

    healed = LinksDB(path)  # pre-existing + needs repair -> incident
    healed.close()

    assert [e for e, _ in recorded] == [incidents.FTS_INDEX_REPAIRED]
    assert recorded[0][1]["store"] == "links"


def test_knowledge_graph_repair_records_an_incident(tmp_path, recorded):
    from navig.memory.knowledge_graph import KnowledgeGraph

    path = tmp_path / "kg.db"
    kg = KnowledgeGraph(path)
    kg._con.executescript(_BROKEN_KG_TRIGGER)
    kg._con.execute("PRAGMA user_version = 0")
    kg._con.commit()
    kg.remember_fact("alice", "lives_in", "Berlin", overwrite=True)
    kg.close()
    recorded.clear()

    healed = KnowledgeGraph(path)
    healed.close()

    assert [e for e, _ in recorded] == [incidents.FTS_INDEX_REPAIRED]
    assert recorded[0][1]["store"] == "knowledge_graph"


# ── but creating a store is NOT an incident ──────────────────────────────────


def test_creating_a_fresh_store_records_nothing(tmp_path, recorded):
    """A repair on a database this call just created is initialisation, not damage.
    Alerting on it would fire on every first run and train the operator to ignore the row."""
    from navig.memory.knowledge_graph import KnowledgeGraph
    from navig.memory.links_db import LinksDB

    LinksDB(tmp_path / "fresh_links.db").close()
    KnowledgeGraph(tmp_path / "fresh_kg.db").close()

    assert recorded == [], f"a brand-new store must be silent, got {recorded}"


def test_reopening_a_healthy_store_records_nothing(tmp_path, recorded):
    """The repair is user_version-gated, so a second open must be silent too."""
    from navig.memory.links_db import LinksDB

    path = tmp_path / "links.db"
    LinksDB(path).close()
    recorded.clear()

    LinksDB(path).close()
    LinksDB(path).close()

    assert recorded == []


# ── a repair that FAILS is reported too ──────────────────────────────────────


def test_an_unrepairable_index_records_the_degradation(tmp_path, recorded, monkeypatch):
    """If the rebuild cannot run, search silently degrades to LIKE — the operator must
    be told, otherwise a failed self-heal looks exactly like a healthy install."""
    from navig.memory import links_db as links_mod
    from navig.memory.links_db import LinksDB

    path = tmp_path / "links.db"
    LinksDB(path).close()
    with sqlite3.connect(str(path)) as con:
        con.execute("PRAGMA user_version = 0")
    recorded.clear()

    def _boom(self):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(links_mod.LinksDB, "_rebuild_fts", _boom)

    db = LinksDB(path)  # must NOT raise — the store stays usable
    try:
        assert [e for e, _ in recorded] == [incidents.FTS_INDEX_UNREPAIRABLE]
        db.add("https://still.works", title="Usable")
        assert len(db.list_all()) == 1
    finally:
        db.close()


# ── the new types are wired into the operator-facing surfaces ────────────────


def test_new_incident_types_have_operator_facing_descriptions():
    """The producer renders DESCRIPTIONS; a type missing one pushes its raw id at the
    operator ("fts_index_repaired"), which is not an explanation."""
    for event in (incidents.FTS_INDEX_REPAIRED, incidents.FTS_INDEX_UNREPAIRABLE):
        assert event in incidents.DESCRIPTIONS
        assert len(incidents.DESCRIPTIONS[event]) > 30
        assert event not in incidents.DESCRIPTIONS[event], "the description must not be the id"


def test_describe_renders_the_new_types(tmp_path):
    entry = {"event": incidents.FTS_INDEX_REPAIRED, "ts": 1_700_000_000}
    rendered = incidents.describe(entry)
    assert "search index" in rendered
    assert incidents.FTS_INDEX_REPAIRED not in rendered  # rendered, not raw


# ── the third external-content store: memory/index.db ────────────────────────
#
# `chunks_fts` (navig.memory.storage) is the third `content=<table>` index in the tree,
# and it was the one store whose triggers could NOT be corrected in place: it used
# `CREATE TRIGGER IF NOT EXISTS`, which never replaces an existing trigger. Its trigger
# text was already correct, so nothing was broken today — but the schema was unable to
# heal, so `navig doctor`'s remedy ("reopening the owning store repairs it") was simply
# false for this database, and any future correction would have reached new installs only.

_BROKEN_CHUNKS_TRIGGER = """
DROP TRIGGER IF EXISTS chunks_ad;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    DELETE FROM chunks_fts WHERE rowid=OLD.rowid;
END;
"""


def _chunks_delete_trigger_sql(path) -> str:
    with sqlite3.connect(str(path)) as con:
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='chunks_ad'"
        ).fetchone()
    return (row[0] if row else "") or ""


def test_reopening_replaces_an_unsafe_chunks_trigger(tmp_path, recorded):
    """The teeth: with `CREATE TRIGGER IF NOT EXISTS` the bad trigger survives forever."""
    from navig.memory.storage import MemoryStorage

    path = tmp_path / "index.db"
    MemoryStorage(path).close()
    with sqlite3.connect(str(path)) as con:
        con.executescript(_BROKEN_CHUNKS_TRIGGER)
    assert "DELETE FROM chunks_fts" in _chunks_delete_trigger_sql(path), "setup failed"

    MemoryStorage(path).close()

    healed = _chunks_delete_trigger_sql(path)
    assert "DELETE FROM chunks_fts" not in healed, (
        "reopening the store left the unsafe trigger in place — doctor's remedy is a lie"
    )
    assert "'delete'" in healed, f"expected the fts5 command syntax, got: {healed}"


def test_memory_storage_repair_records_an_incident(tmp_path, recorded):
    from navig.memory.storage import MemoryStorage

    path = tmp_path / "index.db"
    MemoryStorage(path).close()
    with sqlite3.connect(str(path)) as con:
        con.executescript(_BROKEN_CHUNKS_TRIGGER)
        con.execute("PRAGMA user_version = 0")
    recorded.clear()

    MemoryStorage(path).close()

    assert [e for e, _ in recorded] == [incidents.FTS_INDEX_REPAIRED]
    assert recorded[0][1]["store"] == "memory"


def test_creating_a_fresh_memory_store_records_nothing(tmp_path, recorded):
    """Same noise rule as its two siblings: initialisation is not damage."""
    from navig.memory.storage import MemoryStorage

    MemoryStorage(tmp_path / "fresh_index.db").close()

    assert recorded == [], f"a brand-new store must be silent, got {recorded}"


def test_reopening_a_healthy_memory_store_records_nothing(tmp_path, recorded):
    from navig.memory.storage import MemoryStorage

    path = tmp_path / "index.db"
    MemoryStorage(path).close()
    recorded.clear()

    MemoryStorage(path).close()
    MemoryStorage(path).close()

    assert recorded == []


# ── an UNDAMAGED store must be baselined, not "repaired" ─────────────────────
#
# Every index.db in the field predates FTS_SCHEMA_VERSION, so its user_version is 0.
# The version gate alone therefore cannot tell "was maintained by a broken trigger" from
# "has simply never been stamped" — and chunks_fts's triggers were correct all along.
# Without a damage check the first open after upgrading would rebuild every operator's
# index and record "unsafe sync triggers were corrected" for all of them: a doctor
# warning describing damage nobody had, on every install at once.


def _user_version(path) -> int:
    with sqlite3.connect(str(path)) as con:
        return int(con.execute("PRAGMA user_version").fetchone()[0])


def _seed_chunk(store, content: str = "alpha beta gamma") -> None:
    from navig.memory.storage import MemoryChunk

    store.upsert_chunks(
        [
            MemoryChunk(
                id="c1",
                file_path="notes.md",
                content=content,
                line_start=1,
                line_end=1,
                token_count=3,
            )
        ]
    )


def _fts_hits(path, term: str = "alpha") -> int:
    """Index hits for a term. NOT ``count(*) FROM chunks_fts`` — an external-content
    table reads that straight through to the content table, so it reports the row as
    present even when the index no longer holds a single term for it."""
    with sqlite3.connect(str(path)) as con:
        return int(
            con.execute(
                "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (term,)
            ).fetchone()[0]
        )


def test_an_existing_healthy_store_is_baselined_silently(tmp_path, recorded):
    """The teeth, stated behaviourally so it cannot pass on a missing attribute.

    An index row is removed with the fts5 command syntax, leaving the content table
    intact — precisely the state a rebuild would undo. If the upgrade rebuilds an
    undamaged store, the row comes back and an incident is recorded; both are visible
    without reaching for any internal name.
    """
    from navig.memory.storage import MemoryStorage

    path = tmp_path / "index.db"
    store = MemoryStorage(path)
    _seed_chunk(store)
    store.close()

    with sqlite3.connect(str(path)) as con:
        row = con.execute("SELECT rowid, content, file_path FROM chunks").fetchone()
        con.execute(
            "INSERT INTO chunks_fts(chunks_fts, rowid, content, file_path) "
            "VALUES('delete', ?, ?, ?)",
            row,
        )
        con.execute("PRAGMA user_version = 0")  # what every pre-upgrade install looks like
    assert _fts_hits(path) == 0, "setup failed — the index entry was not removed"
    recorded.clear()

    MemoryStorage(path).close()

    assert recorded == [], f"an undamaged store reported a repair: {recorded}"
    assert _fts_hits(path) == 0, "an undamaged index was rebuilt for nothing"
    assert _user_version(path) == MemoryStorage.FTS_SCHEMA_VERSION, (
        "the store was left unstamped, so it will re-decide this on every open"
    )


def test_a_damaged_store_still_rebuilds(tmp_path, recorded):
    """The other half: the damage check must not smother a real repair."""
    from navig.memory.storage import MemoryStorage

    path = tmp_path / "index.db"
    MemoryStorage(path).close()
    with sqlite3.connect(str(path)) as con:
        con.executescript(_BROKEN_CHUNKS_TRIGGER)
        con.execute("PRAGMA user_version = 0")
    recorded.clear()

    MemoryStorage(path).close()

    assert [e for e, _ in recorded] == [incidents.FTS_INDEX_REPAIRED]
    assert _user_version(path) == MemoryStorage.FTS_SCHEMA_VERSION


def test_damage_is_judged_before_the_triggers_are_replaced(tmp_path, recorded):
    """_init_schema drops the very triggers the decision reads, so order is load-bearing:
    judge after, and every damaged store looks healthy and is silently left desynced."""
    from navig.memory.storage import MemoryStorage

    path = tmp_path / "index.db"
    MemoryStorage(path).close()
    with sqlite3.connect(str(path)) as con:
        con.executescript(_BROKEN_CHUNKS_TRIGGER)
        con.execute("PRAGMA user_version = 0")
    recorded.clear()

    store = MemoryStorage(path)
    try:
        assert store._chunks_triggers_unsafe() is False, (
            "the triggers are safe by now — so a damage check run at this point would "
            "always answer 'healthy', which is why __init__ must ask before _init_schema"
        )
    finally:
        store.close()
    assert [e for e, _ in recorded] == [incidents.FTS_INDEX_REPAIRED]
