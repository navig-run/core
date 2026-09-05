"""`navig doctor` reports the health of the local SQLite stores — honestly.

Two independent signals, both read-only:
  * `PRAGMA quick_check` — page-level corruption.
  * an external-content FTS5 trigger audit — the SCHEMA defect that silently desyncs a
    search index and eventually raises "database disk image is malformed". That is what
    broke editing a bookmark twice (#530c0a21); this reports it *before* data is damaged.

The row must obey the doctor honesty rule: ✓ only when databases were actually verified.
Nothing found, unreadable, or skipped for budget ⇒ ⚠, never green.
"""

from __future__ import annotations

import sqlite3

from navig.commands import doctor


def _row(results):
    assert len(results) == 1, results
    icon, ok, text = results[0]
    return icon, ok, text


def _make_db(path, *, fts_triggers: str = "") -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);
        CREATE VIRTUAL TABLE notes_fts USING fts5(body, content='notes', content_rowid='id');
        """
    )
    if fts_triggers:
        con.executescript(fts_triggers)
    con.execute("INSERT INTO notes (body) VALUES ('hello')")
    con.commit()
    con.close()


_SAFE_TRIGGERS = """
CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, body) VALUES('delete', old.id, old.body);
END;
"""

_UNSAFE_TRIGGERS = """
CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
    DELETE FROM notes_fts WHERE rowid = old.id;
END;
"""


# ── healthy ──────────────────────────────────────────────────────────────────


def test_green_when_databases_are_intact(tmp_path, monkeypatch):
    _make_db(tmp_path / "a.db", fts_triggers=_SAFE_TRIGGERS)
    (tmp_path / "data").mkdir()
    _make_db(tmp_path / "data" / "b.db")
    monkeypatch.setattr(doctor, "config_dir", lambda: tmp_path)

    icon, ok, text = _row(doctor.check_databases())

    assert ok is True
    assert "2 databases intact" in text


def test_scan_skips_backups_and_scratch_trees(tmp_path, monkeypatch):
    _make_db(tmp_path / "real.db")
    for skipped in (".backup", "cache", "spaces"):
        (tmp_path / skipped).mkdir()
        _make_db(tmp_path / skipped / "copy.db")
    monkeypatch.setattr(doctor, "config_dir", lambda: tmp_path)

    assert [p.name for p in doctor._local_databases()] == ["real.db"]


# ── the FTS schema defect (the class that broke bookmarks) ───────────────────


def test_unsafe_external_content_triggers_are_reported(tmp_path, monkeypatch):
    _make_db(tmp_path / "bad.db", fts_triggers=_UNSAFE_TRIGGERS)
    monkeypatch.setattr(doctor, "config_dir", lambda: tmp_path)

    icon, ok, text = _row(doctor.check_databases())

    assert ok is False, "an unsafe search index must not render as a green tick"
    assert icon == doctor._WARN, "a schema defect (not damage yet) is a warning"
    assert "bad.db:notes_fts" in text


def test_safe_command_syntax_triggers_are_not_flagged(tmp_path, monkeypatch):
    _make_db(tmp_path / "good.db", fts_triggers=_SAFE_TRIGGERS)
    monkeypatch.setattr(doctor, "config_dir", lambda: tmp_path)

    _icon, ok, _text = _row(doctor.check_databases())
    assert ok is True


def test_contentless_fts_is_not_mistaken_for_external_content(tmp_path):
    """content='' is a different, legal mode — plain DML on it must not be flagged."""
    path = tmp_path / "c.db"
    con = sqlite3.connect(str(path))
    con.executescript(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, b TEXT);"
        "CREATE VIRTUAL TABLE t_fts USING fts5(b, content='');"
        "CREATE TRIGGER t_ad AFTER DELETE ON t BEGIN DELETE FROM t_fts WHERE rowid=old.id; END;"
    )
    con.commit()
    try:
        assert doctor._external_content_fts_misuse(con) == []
    finally:
        con.close()


def test_standalone_fts_is_not_flagged(tmp_path):
    """A standalone fts table (no content=) may legally be written with plain DML."""
    path = tmp_path / "d.db"
    con = sqlite3.connect(str(path))
    con.executescript(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, b TEXT);"
        "CREATE VIRTUAL TABLE t_fts USING fts5(b);"
        "CREATE TRIGGER t_ad AFTER DELETE ON t BEGIN DELETE FROM t_fts WHERE rowid=old.id; END;"
    )
    con.commit()
    try:
        assert doctor._external_content_fts_misuse(con) == []
    finally:
        con.close()


# ── honesty: could-not-look is never green ───────────────────────────────────


def test_no_databases_found_is_a_warning_not_a_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "config_dir", lambda: tmp_path)

    icon, ok, text = _row(doctor.check_databases())

    assert ok is False
    assert icon == doctor._WARN
    assert "no local databases" in text


def test_an_unreadable_database_is_a_warning_not_a_tick(tmp_path, monkeypatch):
    _make_db(tmp_path / "fine.db")
    (tmp_path / "broken.db").write_bytes(b"this is not a sqlite file at all")
    monkeypatch.setattr(doctor, "config_dir", lambda: tmp_path)

    icon, ok, text = _row(doctor.check_databases())

    assert ok is False, "a database that could not be read must not render green"
    assert icon == doctor._WARN
    assert "broken.db" in text
    assert "1 of 2 verified" in text


def test_corruption_is_an_error_not_a_warning(tmp_path, monkeypatch):
    _make_db(tmp_path / "ok.db")
    monkeypatch.setattr(doctor, "config_dir", lambda: tmp_path)

    def _corrupt(_con):
        return []

    # Force quick_check to report damage without having to physically corrupt a file.
    real_connect = sqlite3.connect

    class _Cursor:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _Con:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *a, **kw):
            if "quick_check" in str(sql):
                return _Cursor(("*** in database main *** page 3 is never used",))
            return self._inner.execute(sql, *a, **kw)

        def close(self):
            self._inner.close()

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: _Con(real_connect(*a, **kw))
    )
    monkeypatch.setattr(doctor, "_external_content_fts_misuse", _corrupt)

    icon, ok, text = _row(doctor.check_databases())

    assert ok is False
    assert icon == doctor._ERR, "real corruption is an error, not a warning"
    assert "corrupt" in text


def test_the_row_is_registered_in_the_storage_section(monkeypatch):
    monkeypatch.setattr(doctor, "check_databases", lambda: [("i", True, "  sentinel-db-row")])
    for name in ("check_storage", "check_vault"):
        monkeypatch.setattr(doctor, name, lambda: [])
    sections = dict(doctor._collect_sections(skip_deps=True))
    assert any("sentinel-db-row" in text for _i, _ok, text in sections["Storage"])


# ── build guard: NAVIG's own schemas must pass the detector ──────────────────


def test_navig_own_stores_use_safe_external_content_triggers(tmp_path):
    """Every store NAVIG itself creates must survive its own detector.

    This is the build-enforced form of the bug that broke bookmark editing
    (#530c0a21) and of the latent one in the knowledge graph: both shipped an
    external-content fts5 index whose sync triggers used plain DML. A new store that
    reintroduces the pattern fails here instead of corrupting a user's database.
    """
    from navig.memory.knowledge_graph import KnowledgeGraph
    from navig.memory.links_db import LinksDB
    from navig.memory.storage import MemoryStorage

    stores = {
        "links.db": lambda p: LinksDB(p),
        "kg.db": lambda p: KnowledgeGraph(p),
        "memory.db": lambda p: MemoryStorage(p),
    }
    offenders: dict[str, list[str]] = {}
    for name, factory in stores.items():
        path = tmp_path / name
        store = factory(path)
        try:
            store.close()
        except Exception:  # noqa: BLE001 - closing is incidental to the check
            pass
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            bad = doctor._external_content_fts_misuse(con)
            if bad:
                offenders[name] = bad
        finally:
            con.close()

    assert not offenders, (
        "an external-content fts5 index must be maintained with the command syntax "
        f"(INSERT INTO t(t) VALUES('delete', …)), not plain DML — offenders: {offenders}"
    )
