"""Tests for navig.importers.sources.firefox.FirefoxImporter."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

import navig.importers.sources.firefox as ff_mod
from navig.importers.sources.firefox import FirefoxImporter


def _make_places_db(path: Path, rows: list[dict]) -> None:
    """Create a minimal moz_places / moz_bookmarks SQLite DB."""
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)"
    )
    con.execute(
        "CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, parent INTEGER, title TEXT, fk INTEGER, type INTEGER)"
    )
    # Insert root folder (type=2)
    con.execute("INSERT INTO moz_bookmarks (id, parent, title, fk, type) VALUES (1, 0, 'Bookmarks Menu', NULL, 2)")

    for i, r in enumerate(rows, start=10):
        place_id = i
        con.execute(
            "INSERT INTO moz_places (id, url, title) VALUES (?, ?, ?)",
            (place_id, r.get("url"), r.get("title")),
        )
        con.execute(
            "INSERT INTO moz_bookmarks (id, parent, title, fk, type) VALUES (?, 1, ?, ?, 1)",
            (i + 100, r.get("title"), place_id),
        )
    con.commit()
    con.close()


class TestFirefoxImporterParse:
    @pytest.fixture
    def importer(self) -> FirefoxImporter:
        return FirefoxImporter()

    def test_returns_empty_for_missing_file(self, importer: FirefoxImporter) -> None:
        result = importer.parse("/nonexistent/places.sqlite")
        assert result == []

    def test_parses_basic_bookmarks(self, importer: FirefoxImporter, tmp_path: Path) -> None:
        db_path = tmp_path / "places.sqlite"
        _make_places_db(db_path, [
            {"url": "https://example.com", "title": "Example"},
            {"url": "https://python.org", "title": "Python"},
        ])
        result = importer.parse(str(db_path))
        assert len(result) == 2

    def test_item_has_url_as_value(self, importer: FirefoxImporter, tmp_path: Path) -> None:
        db_path = tmp_path / "places.sqlite"
        _make_places_db(db_path, [{"url": "https://mozilla.org", "title": "Mozilla"}])
        result = importer.parse(str(db_path))
        assert result[0].value == "https://mozilla.org"

    def test_item_label_from_title(self, importer: FirefoxImporter, tmp_path: Path) -> None:
        db_path = tmp_path / "places.sqlite"
        _make_places_db(db_path, [{"url": "https://x.com", "title": "X"}])
        result = importer.parse(str(db_path))
        assert result[0].label == "X"

    def test_source_is_firefox(self, importer: FirefoxImporter, tmp_path: Path) -> None:
        db_path = tmp_path / "places.sqlite"
        _make_places_db(db_path, [{"url": "https://a.com", "title": "A"}])
        result = importer.parse(str(db_path))
        assert result[0].source == "firefox"

    def test_type_is_bookmark(self, importer: FirefoxImporter, tmp_path: Path) -> None:
        db_path = tmp_path / "places.sqlite"
        _make_places_db(db_path, [{"url": "https://b.com", "title": "B"}])
        result = importer.parse(str(db_path))
        assert result[0].type == "bookmark"

    def test_returns_empty_on_corrupt_db(self, importer: FirefoxImporter, tmp_path: Path) -> None:
        bad_db = tmp_path / "corrupt.sqlite"
        bad_db.write_bytes(b"this is not a sqlite db")
        result = importer.parse(str(bad_db))
        assert result == []


class TestFirefoxImporterDetect:
    def test_detect_returns_false_when_no_default_path(self) -> None:
        importer = FirefoxImporter()
        with patch.object(ff_mod, "firefox_places_default_path", return_value=None):
            assert importer.detect() is False

    def test_detect_returns_true_when_path_exists(self, tmp_path: Path) -> None:
        places = tmp_path / "places.sqlite"
        places.write_bytes(b"")
        importer = FirefoxImporter()
        with patch.object(ff_mod, "firefox_places_default_path", return_value=str(places)):
            assert importer.detect() is True
