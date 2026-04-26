"""Tests for navig.importers.sources.safari — SafariImporter."""
from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.importers.sources.safari import SafariImporter


def _make_importer(path: str | None = None) -> SafariImporter:
    imp = SafariImporter.__new__(SafariImporter)
    imp._path = path
    return imp


def _write_safari_plist(tmp_path: Path, children: list[dict]) -> Path:
    plist_path = tmp_path / "Bookmarks.plist"
    payload = {"Children": children}
    with plist_path.open("wb") as fh:
        plistlib.dump(payload, fh)
    return plist_path


class TestSafariImporterParse:
    def test_returns_empty_for_missing_file(self, tmp_path):
        imp = _make_importer()
        result = imp.parse(str(tmp_path / "nonexistent.plist"))
        assert result == []

    def test_parse_single_bookmark(self, tmp_path):
        children = [{"Title": "OpenAI", "URLString": "https://openai.com"}]
        path = _write_safari_plist(tmp_path, children)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert len(items) == 1
        assert items[0].value == "https://openai.com"
        assert items[0].label == "OpenAI"

    def test_parse_nested_folder(self, tmp_path):
        children = [
            {
                "Title": "Dev",
                "Children": [{"Title": "GitHub", "URLString": "https://github.com"}],
            }
        ]
        path = _write_safari_plist(tmp_path, children)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert len(items) == 1
        assert items[0].meta["folder"] == "Dev"

    def test_bookmark_without_title_uses_url(self, tmp_path):
        children = [{"URLString": "https://example.com"}]
        path = _write_safari_plist(tmp_path, children)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert items[0].label == "https://example.com"

    def test_node_without_url_not_added(self, tmp_path):
        children = [{"Title": "Folder Only"}]  # no URLString
        path = _write_safari_plist(tmp_path, children)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert items == []

    def test_returns_empty_on_corrupt_file(self, tmp_path):
        corrupt = tmp_path / "bad.plist"
        corrupt.write_bytes(b"not a plist")
        imp = _make_importer()
        result = imp.parse(str(corrupt))
        assert result == []

    def test_item_type_is_bookmark(self, tmp_path):
        children = [{"Title": "X", "URLString": "https://x.com"}]
        path = _write_safari_plist(tmp_path, children)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert items[0].type == "bookmark"

    def test_item_source_is_safari(self, tmp_path):
        children = [{"Title": "X", "URLString": "https://x.com"}]
        path = _write_safari_plist(tmp_path, children)
        imp = _make_importer()
        items = imp.parse(str(path))
        assert items[0].source == "safari"


class TestSafariImporterDetect:
    def test_false_when_no_default_path(self):
        imp = _make_importer()
        with patch("navig.importers.sources.safari.safari_default_path", return_value=None):
            assert imp.detect() is False

    def test_false_when_path_does_not_exist(self, tmp_path):
        nonexistent = str(tmp_path / "missing.plist")
        with patch("navig.importers.sources.safari.safari_default_path", return_value=nonexistent):
            imp = _make_importer()
            assert imp.detect() is False

    def test_true_when_file_exists(self, tmp_path):
        f = tmp_path / "Bookmarks.plist"
        f.write_bytes(b"")
        with patch("navig.importers.sources.safari.safari_default_path", return_value=str(f)):
            imp = _make_importer()
            assert imp.detect() is True
