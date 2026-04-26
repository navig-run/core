"""Tests for navig.importers.sources.safari — SafariImporter."""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.importers.sources.safari import SafariImporter


def _write_plist(path: Path, payload: dict) -> None:
    with path.open("wb") as f:
        plistlib.dump(payload, f)


# ---------------------------------------------------------------------------
# SafariImporter metadata
# ---------------------------------------------------------------------------

class TestSafariImporterMeta:
    def test_source_name(self):
        assert SafariImporter.SOURCE_NAME == "safari"

    def test_item_type(self):
        assert SafariImporter.ITEM_TYPE == "bookmark"

    def test_instantiates(self):
        s = SafariImporter()
        assert s is not None


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------

class TestSafariImporterDetect:
    def test_detect_false_when_no_default(self):
        s = SafariImporter()
        with patch.object(s, "default_path", return_value=None):
            assert s.detect() is False

    def test_detect_false_when_file_missing(self, tmp_path):
        s = SafariImporter()
        missing = str(tmp_path / "nonexistent.plist")
        with patch.object(s, "default_path", return_value=missing):
            assert s.detect() is False

    def test_detect_true_when_file_exists(self, tmp_path):
        f = tmp_path / "Bookmarks.plist"
        f.write_bytes(b"data")
        s = SafariImporter()
        with patch.object(s, "default_path", return_value=str(f)):
            assert s.detect() is True


# ---------------------------------------------------------------------------
# parse()
# ---------------------------------------------------------------------------

class TestSafariImporterParse:
    def test_parse_missing_file_returns_empty(self, tmp_path):
        s = SafariImporter()
        result = s.parse(str(tmp_path / "nonexistent.plist"))
        assert result == []

    def test_parse_empty_plist_returns_empty(self, tmp_path):
        f = tmp_path / "bookmarks.plist"
        _write_plist(f, {})
        s = SafariImporter()
        result = s.parse(str(f))
        assert result == []

    def test_parse_single_bookmark(self, tmp_path):
        f = tmp_path / "bookmarks.plist"
        payload = {
            "Children": [
                {"Title": "Example", "URLString": "https://example.com"}
            ]
        }
        _write_plist(f, payload)
        s = SafariImporter()
        result = s.parse(str(f))
        assert len(result) == 1
        assert result[0].value == "https://example.com"
        assert result[0].label == "Example"

    def test_parse_bookmark_source_is_safari(self, tmp_path):
        f = tmp_path / "bookmarks.plist"
        payload = {
            "Children": [
                {"Title": "A", "URLString": "https://a.com"}
            ]
        }
        _write_plist(f, payload)
        result = SafariImporter().parse(str(f))
        assert result[0].source == "safari"

    def test_parse_bookmark_type_is_bookmark(self, tmp_path):
        f = tmp_path / "bookmarks.plist"
        payload = {"Children": [{"Title": "X", "URLString": "https://x.com"}]}
        _write_plist(f, payload)
        result = SafariImporter().parse(str(f))
        assert result[0].type == "bookmark"

    def test_parse_nested_folder(self, tmp_path):
        f = tmp_path / "bookmarks.plist"
        payload = {
            "Children": [
                {
                    "Title": "Work",
                    "Children": [
                        {"Title": "GitHub", "URLString": "https://github.com"}
                    ],
                }
            ]
        }
        _write_plist(f, payload)
        result = SafariImporter().parse(str(f))
        assert len(result) == 1
        assert result[0].meta["folder"] == "Work"

    def test_parse_multiple_bookmarks(self, tmp_path):
        f = tmp_path / "bookmarks.plist"
        payload = {
            "Children": [
                {"Title": "A", "URLString": "https://a.com"},
                {"Title": "B", "URLString": "https://b.com"},
            ]
        }
        _write_plist(f, payload)
        result = SafariImporter().parse(str(f))
        assert len(result) == 2

    def test_parse_node_without_url_skipped(self, tmp_path):
        f = tmp_path / "bookmarks.plist"
        payload = {
            "Children": [
                {"Title": "Folder Only"}  # No URLString
            ]
        }
        _write_plist(f, payload)
        result = SafariImporter().parse(str(f))
        assert result == []

    def test_parse_url_used_as_label_when_no_title(self, tmp_path):
        f = tmp_path / "bookmarks.plist"
        payload = {"Children": [{"URLString": "https://no-title.com"}]}
        _write_plist(f, payload)
        result = SafariImporter().parse(str(f))
        assert result[0].label == "https://no-title.com"

    def test_parse_corrupt_plist_returns_empty(self, tmp_path):
        f = tmp_path / "bad.plist"
        f.write_bytes(b"not a valid plist at all!")
        result = SafariImporter().parse(str(f))
        assert result == []

    def test_parse_folder_path_empty_at_root(self, tmp_path):
        f = tmp_path / "bookmarks.plist"
        payload = {"Children": [{"Title": "X", "URLString": "https://x.com"}]}
        _write_plist(f, payload)
        result = SafariImporter().parse(str(f))
        assert result[0].meta["folder"] == ""

    def test_parse_deeply_nested(self, tmp_path):
        f = tmp_path / "bookmarks.plist"
        payload = {
            "Children": [
                {
                    "Title": "Level1",
                    "Children": [
                        {
                            "Title": "Level2",
                            "Children": [
                                {"Title": "Deep", "URLString": "https://deep.com"}
                            ],
                        }
                    ],
                }
            ]
        }
        _write_plist(f, payload)
        result = SafariImporter().parse(str(f))
        assert len(result) == 1
        assert "Level1" in result[0].meta["folder"]
        assert "Level2" in result[0].meta["folder"]
