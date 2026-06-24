"""
Tests for navig/importers/sources/safari.py
Covers SafariImporter metadata, detect(), parse(), and _walk().
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.importers.sources.safari import SafariImporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_plist(children: list[dict], tmp_path: Path) -> Path:
    """Write a minimal Safari-style plist and return its Path."""
    payload = {"Children": children}
    p = tmp_path / "Bookmarks.plist"
    with p.open("wb") as fh:
        plistlib.dump(payload, fh)
    return p


# ---------------------------------------------------------------------------
# Metadata / construction
# ---------------------------------------------------------------------------

class TestSafariImporterMeta:
    def test_source_name(self):
        assert SafariImporter.SOURCE_NAME == "safari"

    def test_item_type(self):
        assert SafariImporter.ITEM_TYPE == "bookmark"

    def test_instantiates_without_args(self):
        importer = SafariImporter()
        assert importer is not None


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------

class TestSafariImporterDetect:
    def test_detect_true_when_path_exists(self, tmp_path):
        dummy = tmp_path / "Bookmarks.plist"
        dummy.write_bytes(b"")
        with patch("navig.importers.sources.safari.safari_default_path", return_value=str(dummy)):
            assert SafariImporter().detect() is True

    def test_detect_false_when_file_missing(self, tmp_path):
        missing = str(tmp_path / "NoSuch.plist")
        with patch("navig.importers.sources.safari.safari_default_path", return_value=missing):
            assert SafariImporter().detect() is False

    def test_detect_false_when_path_is_none(self):
        with patch("navig.importers.sources.safari.safari_default_path", return_value=None):
            assert SafariImporter().detect() is False

    def test_default_path_returns_safari_default(self):
        with patch("navig.importers.sources.safari.safari_default_path", return_value="/a/b"):
            assert SafariImporter().default_path() == "/a/b"


# ---------------------------------------------------------------------------
# parse() — basic
# ---------------------------------------------------------------------------

class TestSafariImporterParse:
    def test_returns_empty_for_missing_file(self, tmp_path):
        items = SafariImporter().parse(str(tmp_path / "missing.plist"))
        assert items == []

    def test_returns_list_for_valid_plist(self, tmp_path):
        node = {"Title": "Example", "URLString": "https://example.com"}
        p = _build_plist([node], tmp_path)
        items = SafariImporter().parse(str(p))
        assert isinstance(items, list)
        assert len(items) == 1

    def test_item_label_equals_title(self, tmp_path):
        node = {"Title": "My Site", "URLString": "https://my.site"}
        p = _build_plist([node], tmp_path)
        items = SafariImporter().parse(str(p))
        assert items[0].label == "My Site"

    def test_item_value_equals_url(self, tmp_path):
        node = {"Title": "X", "URLString": "https://x.com"}
        p = _build_plist([node], tmp_path)
        items = SafariImporter().parse(str(p))
        assert items[0].value == "https://x.com"

    def test_item_source_is_safari(self, tmp_path):
        node = {"Title": "T", "URLString": "https://t.co"}
        p = _build_plist([node], tmp_path)
        items = SafariImporter().parse(str(p))
        assert items[0].source == "safari"

    def test_item_type_is_bookmark(self, tmp_path):
        node = {"Title": "T", "URLString": "https://t.co"}
        p = _build_plist([node], tmp_path)
        items = SafariImporter().parse(str(p))
        assert items[0].type == "bookmark"

    def test_missing_title_uses_url_as_label(self, tmp_path):
        node = {"URLString": "https://no-title.com"}
        p = _build_plist([node], tmp_path)
        items = SafariImporter().parse(str(p))
        assert items[0].label == "https://no-title.com"

    def test_empty_url_skipped(self, tmp_path):
        node = {"Title": "Folder", "URLString": ""}
        p = _build_plist([node], tmp_path)
        items = SafariImporter().parse(str(p))
        assert items == []

    def test_node_without_url_skipped(self, tmp_path):
        node = {"Title": "No URL"}
        p = _build_plist([node], tmp_path)
        items = SafariImporter().parse(str(p))
        assert items == []

    def test_empty_children_returns_empty(self, tmp_path):
        p = _build_plist([], tmp_path)
        items = SafariImporter().parse(str(p))
        assert items == []

    def test_corrupt_plist_returns_empty(self, tmp_path):
        p = tmp_path / "bad.plist"
        p.write_bytes(b"NOT A PLIST <<>>")
        items = SafariImporter().parse(str(p))
        assert items == []

    def test_multiple_bookmarks(self, tmp_path):
        nodes = [
            {"Title": "A", "URLString": "https://a.com"},
            {"Title": "B", "URLString": "https://b.com"},
            {"Title": "C", "URLString": "https://c.com"},
        ]
        p = _build_plist(nodes, tmp_path)
        items = SafariImporter().parse(str(p))
        assert len(items) == 3
        assert {i.label for i in items} == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# parse() — nested folders (_walk recursion)
# ---------------------------------------------------------------------------

class TestSafariImporterNested:
    def test_nested_folder_items_included(self, tmp_path):
        tree = [
            {
                "Title": "Folder",
                "Children": [{"Title": "Inner", "URLString": "https://inner.com"}],
            }
        ]
        p = _build_plist(tree, tmp_path)
        items = SafariImporter().parse(str(p))
        assert len(items) == 1
        assert items[0].label == "Inner"

    def test_folder_name_in_meta(self, tmp_path):
        tree = [
            {
                "Title": "Work",
                "Children": [{"Title": "Task", "URLString": "https://task.com"}],
            }
        ]
        p = _build_plist(tree, tmp_path)
        items = SafariImporter().parse(str(p))
        assert "Work" in items[0].meta["folder"]

    def test_deeply_nested_folder_path(self, tmp_path):
        tree = [
            {
                "Title": "A",
                "Children": [
                    {
                        "Title": "B",
                        "Children": [{"Title": "Leaf", "URLString": "https://leaf.com"}],
                    }
                ],
            }
        ]
        p = _build_plist(tree, tmp_path)
        items = SafariImporter().parse(str(p))
        assert len(items) == 1
        folder = items[0].meta["folder"]
        assert "A" in folder
        assert "B" in folder

    def test_top_level_item_empty_folder(self, tmp_path):
        node = {"Title": "Top", "URLString": "https://top.com"}
        p = _build_plist([node], tmp_path)
        items = SafariImporter().parse(str(p))
        assert items[0].meta["folder"] == ""

    def test_folder_url_and_child_both_collected(self, tmp_path):
        # A node with BOTH a URLString AND Children — url becomes item, children recurse
        tree = [
            {
                "Title": "Both",
                "URLString": "https://both.com",
                "Children": [{"Title": "Child", "URLString": "https://child.com"}],
            }
        ]
        p = _build_plist(tree, tmp_path)
        items = SafariImporter().parse(str(p))
        urls = {i.value for i in items}
        assert "https://both.com" in urls
        assert "https://child.com" in urls
