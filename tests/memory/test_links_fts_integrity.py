"""The bookmark store survives editing — and repairs databases the old triggers broke.

`links_fts` is an EXTERNAL-CONTENT FTS5 table (`content='links'`), so its index may only
be maintained with the special command syntax. The original triggers used a plain
`UPDATE links_fts SET …` / `DELETE FROM links_fts …`, which is illegal for `content=`
tables: the first edit of a row silently desynced the index (the OLD title stayed
searchable) and the SECOND edit of that row raised
`sqlite3.DatabaseError: database disk image is malformed` — editing the same bookmark
twice broke the whole store.

These pin the repaired behaviour, the one-time migration of an already-damaged database,
and the two read/write-safety fixes that ride along.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from navig.memory.links_db import LinkRecord, LinksDB

# The pre-fix schema, so the migration can be tested against a real broken database.
_BROKEN_TRIGGERS = """
DROP TRIGGER IF EXISTS links_fts_insert;
DROP TRIGGER IF EXISTS links_fts_update;
DROP TRIGGER IF EXISTS links_fts_delete;

CREATE TRIGGER links_fts_insert AFTER INSERT ON links BEGIN
    INSERT INTO links_fts(rowid, id, url, title, notes, tags)
    VALUES (new.rowid, new.id, new.url, COALESCE(new.title,''), COALESCE(new.notes,''), COALESCE(new.tags,''));
END;

CREATE TRIGGER links_fts_update AFTER UPDATE ON links BEGIN
    UPDATE links_fts SET url=new.url, title=COALESCE(new.title,''), notes=COALESCE(new.notes,''), tags=COALESCE(new.tags,'')
    WHERE id=new.id;
END;

CREATE TRIGGER links_fts_delete AFTER DELETE ON links BEGIN
    DELETE FROM links_fts WHERE id=old.id;
END;
"""


def _downgrade_to_broken(db: LinksDB) -> None:
    """Put the pre-fix triggers back and clear the migration marker."""
    db._con.executescript(_BROKEN_TRIGGERS)
    db._con.execute("PRAGMA user_version = 0")
    db._con.commit()


# ── the bug itself ───────────────────────────────────────────────────────────


def test_editing_the_same_bookmark_twice_works(tmp_path):
    """The reported failure: the SECOND edit of a row raised 'database disk image is
    malformed'. Any number of edits must now succeed and stay searchable."""
    db = LinksDB(tmp_path / "links.db")
    try:
        a = db.add("https://a.example", title="Alpha", tags=["x"])
        db.add("https://b.example", title="Beta", tags=["y"])

        db.update(a, title="AlphaEdited")
        db.update(a, notes="note")  # pre-fix: DatabaseError here
        db.update(a, title="AlphaAgain")

        assert [r.title for r in db.search("AlphaAgain")] == ["AlphaAgain"]
        assert db.get(a).notes == "note"
    finally:
        db.close()


def test_edit_does_not_leave_the_old_text_searchable(tmp_path):
    """The first edit used to leave stale terms indexed — searching the OLD title still
    matched. A correct 'delete'-then-insert removes them."""
    db = LinksDB(tmp_path / "links.db")
    try:
        a = db.add("https://a.example", title="Alpha")
        db.update(a, title="Renamed")

        assert [r.title for r in db.search("Renamed")] == ["Renamed"]
        assert db.search("Alpha") == [], "the pre-edit title must no longer match"
    finally:
        db.close()


def test_delete_removes_the_row_from_search(tmp_path):
    db = LinksDB(tmp_path / "links.db")
    try:
        a = db.add("https://a.example", title="Doomed")
        db.add("https://b.example", title="Keeper")
        assert db.delete(a) is True

        assert db.search("Doomed") == []
        assert [r.title for r in db.search("Keeper")] == ["Keeper"]
        # the index is still usable afterwards (a bad delete corrupted it too)
        db.update(db.add("https://c.example", title="Third"), title="ThirdEdited")
        assert [r.title for r in db.search("ThirdEdited")] == ["ThirdEdited"]
    finally:
        db.close()


# ── the migration for already-damaged databases ──────────────────────────────


def test_reopening_repairs_a_database_damaged_by_the_old_triggers(tmp_path):
    """A database written by the broken triggers must be healed on the next open:
    triggers swapped AND the index rebuilt (the swap alone leaves it inconsistent)."""
    path = tmp_path / "links.db"
    db = LinksDB(path)
    try:
        _downgrade_to_broken(db)
        a = db.add("https://a.example", title="Alpha")
        db.update(a, title="Renamed")  # damages the index the old way
        assert db.search("Alpha"), "sanity: the stale term is indexed pre-repair"
    finally:
        db.close()

    healed = LinksDB(path)  # reopening runs the migration
    try:
        assert healed.search("Alpha") == [], "rebuild must drop the stale term"
        assert [r.title for r in healed.search("Renamed")] == ["Renamed"]
        # and the store is editable again
        healed.update(a, notes="n")
        healed.update(a, notes="n2")
        assert healed.get(a).notes == "n2"
    finally:
        healed.close()


def test_repair_runs_once_not_on_every_open(tmp_path):
    path = tmp_path / "links.db"
    db = LinksDB(path)
    db.close()

    reopened = LinksDB(path)
    try:
        version = reopened._con.execute("PRAGMA user_version").fetchone()[0]
        assert version >= 1, "the migration marker must be persisted"
    finally:
        reopened.close()


def test_search_falls_back_instead_of_raising_on_a_damaged_index(tmp_path):
    """search() caught only OperationalError, but a damaged index raises its PARENT
    (DatabaseError) — so search hard-failed on exactly the databases needing a fallback."""
    class _MalformedOnFTS:
        """Raises the real corruption error on the FTS query, passes everything else."""

        def __init__(self, real):
            self._real = real
            self._raised = False

        def execute(self, sql, *args, **kw):
            if "links_fts" in str(sql) and not self._raised:
                self._raised = True
                raise sqlite3.DatabaseError("database disk image is malformed")
            return self._real.execute(sql, *args, **kw)

        def __getattr__(self, name):
            return getattr(self._real, name)

    db = LinksDB(tmp_path / "links.db")
    try:
        db.add("https://a.example", title="Findme")
        real = db._con
        db._con = _MalformedOnFTS(real)  # type: ignore[assignment]

        results = db.search("Findme")  # must degrade to LIKE, not raise

        db._con = real  # type: ignore[assignment]
        assert [r.title for r in results] == ["Findme"]
    finally:
        db.close()


# ── the read/write-safety fixes that ride along ──────────────────────────────


def test_link_list_survives_one_corrupt_tags_blob(tmp_path):
    """LinkRecord is built in `[LinkRecord(dict(r)) for r in rows]`, so a corrupt tags
    blob used to blank the WHOLE bookmark list."""
    db = LinksDB(tmp_path / "links.db")
    try:
        bad = db.add("https://bad.example", title="Bad")
        db.add("https://good.example", title="Good")
        db._con.execute("UPDATE links SET tags=? WHERE id=?", ("{not json", bad))
        db._con.commit()

        links = db.list_all()

        assert len(links) == 2
        assert next(x for x in links if x.id == bad).tags == []
        assert next(x for x in links if x.id != bad).title == "Good"
    finally:
        db.close()


def test_update_does_not_clobber_a_corrupt_tags_blob(tmp_path):
    """The degrade is read-side ONLY: editing the title must leave the stored (corrupt)
    tags exactly as they were, not persist the [] the read degraded to."""
    db = LinksDB(tmp_path / "links.db")
    try:
        link_id = db.add("https://x.example", title="Before", tags=["keep"])
        db._con.execute("UPDATE links SET tags=? WHERE id=?", ("{not json", link_id))
        db._con.commit()

        assert db.update(link_id, title="After") is True

        row = db._con.execute("SELECT title, tags FROM links WHERE id=?", (link_id,)).fetchone()
        assert row[0] == "After"
        assert row[1] == "{not json", "the original blob must survive an unrelated edit"
    finally:
        db.close()


def test_update_still_writes_tags_when_supplied(tmp_path):
    db = LinksDB(tmp_path / "links.db")
    try:
        link_id = db.add("https://y.example", tags=["old"])
        assert db.update(link_id, tags=["new", "tags"]) is True
        row = db._con.execute("SELECT tags FROM links WHERE id=?", (link_id,)).fetchone()
        assert json.loads(row[0]) == ["new", "tags"]
        assert db.get(link_id).tags == ["new", "tags"]
    finally:
        db.close()


def test_link_record_degrades_a_corrupt_blob():
    rec = LinkRecord(
        {"id": "1", "url": "u", "tags": "{not json", "created_at": "2026-01-01T00:00:00"}
    )
    assert rec.tags == []
    assert rec.url == "u"


@pytest.mark.parametrize("tags", [None, [], ["a"], ["a", "b"]])
def test_tags_round_trip_unchanged(tmp_path, tags):
    db = LinksDB(tmp_path / "links.db")
    try:
        link_id = db.add("https://z.example", tags=tags)
        assert db.get(link_id).tags == (tags or [])
    finally:
        db.close()
