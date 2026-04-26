"""Tests for navig.memory.sync — _as_chunk, import_chunks."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.memory.sync import _as_chunk, import_chunks


class TestAsChunk:
    def test_none_on_missing_content(self):
        assert _as_chunk({}, "default_file.py") is None

    def test_none_on_empty_content(self):
        assert _as_chunk({"content": "   "}, "default_file.py") is None

    def test_none_on_non_string_content(self):
        assert _as_chunk({"content": 42}, "default_file.py") is None

    def test_basic_chunk(self):
        c = _as_chunk({"content": "hello", "id": "abc"}, "file.py")
        assert c is not None
        assert c.id == "abc"
        assert c.content == "hello"

    def test_generated_id_when_missing(self):
        c = _as_chunk({"content": "world"}, "file.py")
        assert c is not None
        assert c.id.startswith("sync::")

    def test_defaults_for_line_numbers(self):
        c = _as_chunk({"content": "x"}, "file.py")
        assert c.line_start == 1
        assert c.line_end == 1

    def test_file_path_from_item(self):
        c = _as_chunk({"content": "x", "file_path": "src/foo.py"}, "default.py")
        assert c.file_path == "src/foo.py"

    def test_file_path_defaults_to_default(self):
        c = _as_chunk({"content": "x"}, "remote/default/sync")
        assert c.file_path == "remote/default/sync"

    def test_metadata_string_parsed_as_json(self):
        c = _as_chunk({"content": "x", "metadata": '{"key": "val"}'}, "f.py")
        assert c.metadata == {"key": "val"}

    def test_metadata_invalid_json_wrapped(self):
        c = _as_chunk({"content": "x", "metadata": "not-json"}, "f.py")
        assert c.metadata == {"raw_metadata": "not-json"}

    def test_metadata_non_dict_becomes_empty(self):
        c = _as_chunk({"content": "x", "metadata": [1, 2]}, "f.py")
        assert c.metadata == {}


class TestImportChunks:
    def test_returns_imported_and_skipped(self, tmp_path):
        db = tmp_path / "mem.db"
        chunks = [{"content": "hello", "id": "a"}, {"content": "world", "id": "b"}]
        with patch("navig.memory.sync.MemoryStorage") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.upsert_chunks.return_value = 2
            mock_cls.return_value = mock_instance
            imported, skipped = import_chunks(db, chunks)
        assert imported == 2
        assert skipped == 0

    def test_skips_non_dict_items(self, tmp_path):
        db = tmp_path / "mem.db"
        chunks = ["not-a-dict", {"content": "ok", "id": "x"}]
        with patch("navig.memory.sync.MemoryStorage") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.upsert_chunks.return_value = 1
            mock_cls.return_value = mock_instance
            imported, skipped = import_chunks(db, chunks)
        assert skipped == 1

    def test_skips_empty_content(self, tmp_path):
        db = tmp_path / "mem.db"
        chunks = [{"content": ""}, {"content": "real", "id": "y"}]
        with patch("navig.memory.sync.MemoryStorage") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.upsert_chunks.return_value = 1
            mock_cls.return_value = mock_instance
            _, skipped = import_chunks(db, chunks)
        assert skipped == 1

    def test_formation_in_default_file(self, tmp_path):
        db = tmp_path / "mem.db"
        with patch("navig.memory.sync.MemoryStorage") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.upsert_chunks.return_value = 0
            mock_cls.return_value = mock_instance
            import_chunks(db, [], formation="prod")
        # The storage is constructed with the db path
        mock_cls.assert_called_once_with(db)

    def test_empty_list_returns_zero_zero(self, tmp_path):
        db = tmp_path / "mem.db"
        with patch("navig.memory.sync.MemoryStorage") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.upsert_chunks.return_value = 0
            mock_cls.return_value = mock_instance
            imported, skipped = import_chunks(db, [])
        assert imported == 0
        assert skipped == 0
