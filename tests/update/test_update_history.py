"""Hermetic unit tests for navig.update.history."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.update.history import UpdateHistory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hist(tmp_path: Path, keep: int = 50) -> UpdateHistory:
    return UpdateHistory(cache_dir=str(tmp_path), keep=keep)


def _rec(node_id: str = "node1", host: str = "prod", version: str = "1.0") -> dict:
    return {"node_id": node_id, "host": host, "version": version, "ts": "2024-01-01"}


def _plain_write(path: Path, text: str) -> None:
    """Helper that bypasses atomic_write_text to directly write test data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# append + read
# ---------------------------------------------------------------------------


class TestAppendRead:
    def test_empty_returns_empty(self, tmp_path):
        h = _hist(tmp_path)
        assert h.read() == []

    def test_append_then_read(self, tmp_path):
        h = _hist(tmp_path)
        with patch("navig.update.history._atomic_write_text", side_effect=lambda p, t: _plain_write(p, t)):
            h.append(_rec())
        entries = h.read()
        assert len(entries) == 1
        assert entries[0]["node_id"] == "node1"

    def test_multiple_appends_newest_first(self, tmp_path):
        h = _hist(tmp_path)

        def plain_append(path, text):
            _plain_write(path, text)

        with patch("navig.update.history._atomic_write_text", side_effect=plain_append):
            h.append(_rec(version="1.0"))
            h.append(_rec(version="2.0"))

        entries = h.read()
        assert entries[0]["version"] == "2.0"
        assert entries[1]["version"] == "1.0"

    def test_read_limit(self, tmp_path):
        h = _hist(tmp_path)
        path = tmp_path / UpdateHistory._FILENAME
        lines = [json.dumps(_rec(version=str(i))) for i in range(10)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert len(h.read(limit=3)) == 3

    def test_read_limit_larger_than_count(self, tmp_path):
        h = _hist(tmp_path)
        path = tmp_path / UpdateHistory._FILENAME
        lines = [json.dumps(_rec()) for _ in range(5)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert len(h.read(limit=100)) == 5


# ---------------------------------------------------------------------------
# filtering
# ---------------------------------------------------------------------------


class TestFiltering:
    def _write_recs(self, h: UpdateHistory, recs: list[dict]) -> None:
        path = h._path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")

    def test_filter_by_node_id(self, tmp_path):
        h = _hist(tmp_path)
        self._write_recs(h, [_rec(node_id="n1"), _rec(node_id="n2")])
        result = h.read(node_id="n1")
        assert all(e["node_id"] == "n1" for e in result)
        assert len(result) == 1

    def test_filter_by_host_matches_node_id_field(self, tmp_path):
        # host param uses node_id field for filtering (same logic path as node_id)
        h = _hist(tmp_path)
        self._write_recs(h, [_rec(node_id="prod"), _rec(node_id="staging")])
        result = h.read(host="prod")
        assert len(result) == 1

    def test_no_match_returns_empty(self, tmp_path):
        h = _hist(tmp_path)
        self._write_recs(h, [_rec(node_id="n1")])
        assert h.read(node_id="other") == []


# ---------------------------------------------------------------------------
# keep / pruning
# ---------------------------------------------------------------------------


class TestKeepPruning:
    def test_keeps_most_recent_n_on_append(self, tmp_path):
        h = _hist(tmp_path, keep=3)
        path = tmp_path / UpdateHistory._FILENAME
        existing = [json.dumps(_rec(version=str(i))) for i in range(5)]
        path.write_text("\n".join(existing) + "\n", encoding="utf-8")

        with patch("navig.update.history._atomic_write_text", side_effect=lambda p, t: _plain_write(p, t)):
            h.append(_rec(version="new"))

        entries = h.read(limit=100)
        versions = [e["version"] for e in entries]
        assert "new" in versions
        assert len(entries) == 3


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_removes_file(self, tmp_path):
        h = _hist(tmp_path)
        path = tmp_path / UpdateHistory._FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_rec()) + "\n", encoding="utf-8")
        h.clear()
        assert not path.exists()

    def test_clear_idempotent_when_no_file(self, tmp_path):
        h = _hist(tmp_path)
        # Should not raise
        h.clear()

    def test_read_after_clear_returns_empty(self, tmp_path):
        h = _hist(tmp_path)
        path = tmp_path / UpdateHistory._FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_rec()) + "\n", encoding="utf-8")
        h.clear()
        assert h.read() == []
