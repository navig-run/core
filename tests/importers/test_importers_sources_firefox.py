"""
Tests for navig/importers/sources/firefox.py
Covers FirefoxImporter using real SQLite databases created in tmp_path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.importers.sources.firefox import FirefoxImporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_db(tmp_path: Path, bookmarks=None, folders=None) -> Path:
    """Create a minimal Firefox places.sqlite in tmp_path and return its path."""
    db_path = tmp_path / "places.sqlite"
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE moz_places (id INTEGER PRIMARY KEY, title TEXT, url TEXT)"
    )
    con.execute(
        "CREATE TABLE moz_bookmarks "
        "(id INTEGER PRIMARY KEY, parent INTEGER, type INTEGER, fk INTEGER, title TEXT)"
    )

    # Insert folders (type=2)
    for folder in (folders or []):
        con.execute(
            "INSERT INTO moz_bookmarks (id, parent, type, fk, title) VALUES (?, ?, 2, NULL, ?)",
            (folder["id"], folder.get("parent", 0), folder.get("title", "")),
        )

    # Insert bookmarks (type=1)
    for bm in (bookmarks or []):
        place_id = bm["place_id"]
        con.execute(
            "INSERT INTO moz_places (id, title, url) VALUES (?, ?, ?)",
            (place_id, bm.get("place_title"), bm["url"]),
        )
        con.execute(
            "INSERT INTO moz_bookmarks (id, parent, type, fk, title) VALUES (?, ?, 1, ?, ?)",
            (bm["bm_id"], bm.get("parent", 0), place_id, bm.get("bm_title")),
        )

    con.commit()
    con.close()
    return db_path


# ---------------------------------------------------------------------------
# Metadata / construction
# ---------------------------------------------------------------------------

class TestFirefoxImporterMeta:
    def test_source_name(self):
        assert FirefoxImporter.SOURCE_NAME == "firefox"

    def test_item_type(self):
        assert FirefoxImporter.ITEM_TYPE == "bookmark"

    def test_instantiates_without_args(self):
        assert FirefoxImporter() is not None


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------

class TestFirefoxImporterDetect:
    def test_detect_true_when_file_exists(self, tmp_path):
        db = _create_db(tmp_path)
        with patch("navig.importers.sources.firefox.firefox_places_default_path", return_value=str(db)):
            assert FirefoxImporter().detect() is True

    def test_detect_false_when_file_missing(self, tmp_path):
        missing = str(tmp_path / "places.sqlite")
        with patch("navig.importers.sources.firefox.firefox_places_default_path", return_value=missing):
            assert FirefoxImporter().detect() is False

    def test_detect_false_when_path_is_none(self):
        with patch("navig.importers.sources.firefox.firefox_places_default_path", return_value=None):
            assert FirefoxImporter().detect() is False

    def test_default_path_returns_firefox_default(self):
        with patch("navig.importers.sources.firefox.firefox_places_default_path", return_value="/x/y"):
            assert FirefoxImporter().default_path() == "/x/y"


# ---------------------------------------------------------------------------
# parse() — basic
# ---------------------------------------------------------------------------

class TestFirefoxImporterParse:
    def test_returns_empty_for_missing_file(self, tmp_path):
        items = FirefoxImporter().parse(str(tmp_path / "missing.sqlite"))
        assert items == []

    def test_returns_list_for_valid_db(self, tmp_path):
        db = _create_db(
            tmp_path,
            bookmarks=[{"bm_id": 10, "place_id": 1, "url": "https://a.com", "bm_title": "A"}],
        )
        items = FirefoxImporter().parse(str(db))
        assert isinstance(items, list)

    def test_single_bookmark_value(self, tmp_path):
        db = _create_db(
            tmp_path,
            bookmarks=[{"bm_id": 10, "place_id": 1, "url": "https://example.com", "bm_title": "Ex"}],
        )
        items = FirefoxImporter().parse(str(db))
        assert len(items) == 1
        assert items[0].value == "https://example.com"

    def test_single_bookmark_label_from_bm_title(self, tmp_path):
        db = _create_db(
            tmp_path,
            bookmarks=[{"bm_id": 10, "place_id": 1, "url": "https://x.com", "bm_title": "MyLabel"}],
        )
        items = FirefoxImporter().parse(str(db))
        assert items[0].label == "MyLabel"

    def test_source_is_firefox(self, tmp_path):
        db = _create_db(
            tmp_path,
            bookmarks=[{"bm_id": 10, "place_id": 1, "url": "https://x.com", "bm_title": "T"}],
        )
        items = FirefoxImporter().parse(str(db))
        assert items[0].source == "firefox"

    def test_type_is_bookmark(self, tmp_path):
        db = _create_db(
            tmp_path,
            bookmarks=[{"bm_id": 10, "place_id": 1, "url": "https://x.com", "bm_title": "T"}],
        )
        items = FirefoxImporter().parse(str(db))
        assert items[0].type == "bookmark"

    def test_empty_url_skipped(self, tmp_path):
        db_path = tmp_path / "places.sqlite"
        con = sqlite3.connect(str(db_path))
        con.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, title TEXT, url TEXT)")
        con.execute("CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, parent INTEGER, type INTEGER, fk INTEGER, title TEXT)")
        con.execute("INSERT INTO moz_places VALUES (1, 'NoURL', '')")
        con.execute("INSERT INTO moz_bookmarks VALUES (10, 0, 1, 1, 'T')")
        con.commit()
        con.close()
        items = FirefoxImporter().parse(str(db_path))
        assert items == []

    def test_multiple_bookmarks(self, tmp_path):
        db = _create_db(
            tmp_path,
            bookmarks=[
                {"bm_id": 10, "place_id": 1, "url": "https://a.com", "bm_title": "A"},
                {"bm_id": 11, "place_id": 2, "url": "https://b.com", "bm_title": "B"},
                {"bm_id": 12, "place_id": 3, "url": "https://c.com", "bm_title": "C"},
            ],
        )
        items = FirefoxImporter().parse(str(db))
        assert len(items) == 3

    def test_corrupt_db_returns_empty(self, tmp_path):
        p = tmp_path / "bad.sqlite"
        p.write_bytes(b"NOT A SQLITE FILE !!!")
        items = FirefoxImporter().parse(str(p))
        assert items == []

    def test_meta_contains_folder_key(self, tmp_path):
        db = _create_db(
            tmp_path,
            bookmarks=[{"bm_id": 10, "place_id": 1, "url": "https://x.com", "bm_title": "X"}],
        )
        items = FirefoxImporter().parse(str(db))
        assert "folder" in items[0].meta


# ---------------------------------------------------------------------------
# parse() — with folders
# ---------------------------------------------------------------------------

class TestFirefoxImporterFolders:
    def test_bookmark_in_folder(self, tmp_path):
        db = _create_db(
            tmp_path,
            folders=[{"id": 5, "parent": 0, "title": "Work"}],
            bookmarks=[{"bm_id": 10, "place_id": 1, "url": "https://work.com", "bm_title": "T", "parent": 5}],
        )
        items = FirefoxImporter().parse(str(db))
        assert len(items) == 1
        assert "Work" in items[0].meta["folder"]

    def test_nested_folder_chain(self, tmp_path):
        db = _create_db(
            tmp_path,
            folders=[
                {"id": 5, "parent": 0, "title": "Root"},
                {"id": 6, "parent": 5, "title": "Sub"},
            ],
            bookmarks=[{"bm_id": 10, "place_id": 1, "url": "https://n.com", "bm_title": "N", "parent": 6}],
        )
        items = FirefoxImporter().parse(str(db))
        folder = items[0].meta["folder"]
        assert "Root" in folder
        assert "Sub" in folder

    def test_top_level_bookmark_empty_folder(self, tmp_path):
        # parent not in folders dict → empty folder string
        db = _create_db(
            tmp_path,
            bookmarks=[{"bm_id": 10, "place_id": 1, "url": "https://top.com", "bm_title": "Top", "parent": 999}],
        )
        items = FirefoxImporter().parse(str(db))
        assert items[0].meta["folder"] == ""


# ---------------------------------------------------------------------------
# _resolve_folder_chain
# ---------------------------------------------------------------------------

class TestResolveFolderChain:
    def test_empty_when_unknown_parent(self):
        importer = FirefoxImporter()
        result = importer._resolve_folder_chain(999, {})
        assert result == ""

    def test_single_folder(self):
        folders = {5: {"title": "Books", "parent": 0}}
        result = FirefoxImporter()._resolve_folder_chain(5, folders)
        assert result == "Books"

    def test_two_level_chain(self):
        folders = {
            1: {"title": "A", "parent": 0},
            2: {"title": "B", "parent": 1},
        }
        result = FirefoxImporter()._resolve_folder_chain(2, folders)
        assert "A" in result
        assert "B" in result

    def test_cycle_protection(self):
        # Circular references must not cause infinite loop
        folders = {
            1: {"title": "X", "parent": 2},
            2: {"title": "Y", "parent": 1},
        }
        result = FirefoxImporter()._resolve_folder_chain(1, folders)
        assert isinstance(result, str)

    def test_unnamed_folder_skipped(self):
        folders = {5: {"title": "", "parent": 0}}
        result = FirefoxImporter()._resolve_folder_chain(5, folders)
        assert result == ""
