"""Tests for navig.importers.sources.chrome — ChromeImporter."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.importers.sources.chrome import ChromeImporter


def _make_importer() -> ChromeImporter:
    return ChromeImporter.__new__(ChromeImporter)


def _write_chrome_bookmarks(tmp_path: Path, roots: dict) -> Path:
    p = tmp_path / "Bookmarks"
    p.write_text(json.dumps({"roots": roots}), encoding="utf-8")
    return p


class TestChromeImporterParse:
    def test_returns_empty_for_missing_file(self, tmp_path):
        imp = _make_importer()
        result = imp.parse(str(tmp_path / "nonexistent"))
        assert result == []

    def test_parses_bookmark_bar(self, tmp_path):
        roots = {
            "bookmark_bar": {
                "children": [{"type": "url", "name": "Google", "url": "https://google.com"}]
            }
        }
        path = _write_chrome_bookmarks(tmp_path, roots)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert len(items) == 1
        assert items[0].value == "https://google.com"
        assert items[0].label == "Google"

    def test_parses_other_root(self, tmp_path):
        roots = {
            "other": {
                "children": [{"type": "url", "name": "Other", "url": "https://other.com"}]
            }
        }
        path = _write_chrome_bookmarks(tmp_path, roots)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert len(items) == 1

    def test_parses_synced_root(self, tmp_path):
        roots = {
            "synced": {
                "children": [{"type": "url", "name": "Synced", "url": "https://synced.com"}]
            }
        }
        path = _write_chrome_bookmarks(tmp_path, roots)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert len(items) == 1

    def test_parses_nested_folder(self, tmp_path):
        roots = {
            "bookmark_bar": {
                "children": [
                    {
                        "type": "folder",
                        "name": "Dev",
                        "children": [{"type": "url", "name": "GH", "url": "https://github.com"}],
                    }
                ]
            }
        }
        path = _write_chrome_bookmarks(tmp_path, roots)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert len(items) == 1
        assert "Dev" in items[0].meta["folder"]

    def test_skips_url_node_without_url(self, tmp_path):
        roots = {
            "bookmark_bar": {"children": [{"type": "url", "name": "NoUrl"}]}  # no "url" key
        }
        path = _write_chrome_bookmarks(tmp_path, roots)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert items == []

    def test_returns_empty_on_invalid_json(self, tmp_path):
        bad = tmp_path / "Bookmarks"
        bad.write_text("NOT JSON")
        imp = _make_importer()
        result = imp.parse(str(bad))
        assert result == []

    def test_item_type_and_source(self, tmp_path):
        roots = {
            "bookmark_bar": {
                "children": [{"type": "url", "name": "X", "url": "https://x.com"}]
            }
        }
        path = _write_chrome_bookmarks(tmp_path, roots)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert items[0].type == "bookmark"
        assert items[0].source == "chrome"


class TestChromeImporterDetect:
    def test_false_when_no_default_path(self):
        imp = _make_importer()
        with patch("navig.importers.sources.chrome.chrome_default_path", return_value=None):
            assert imp.detect() is False

    def test_false_when_path_does_not_exist(self, tmp_path):
        with patch(
            "navig.importers.sources.chrome.chrome_default_path",
            return_value=str(tmp_path / "missing"),
        ):
            assert _make_importer().detect() is False

    def test_true_when_file_exists(self, tmp_path):
        f = tmp_path / "Bookmarks"
        f.write_text("{}")
        with patch(
            "navig.importers.sources.chrome.chrome_default_path", return_value=str(f)
        ):
            assert _make_importer().detect() is True
