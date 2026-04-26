"""Tests for navig.deploy.history.DeployHistory."""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.deploy.history import DeployHistory


def _history(tmp_path: Path, keep: int = 50) -> DeployHistory:
    return DeployHistory(cache_dir=tmp_path / "cache", keep=keep)


class TestDeployHistoryAppendAndRead:
    def test_read_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        h = _history(tmp_path)
        assert h.read() == []

    def test_append_creates_file(self, tmp_path: Path) -> None:
        h = _history(tmp_path)
        h.append({"app": "myapp", "host": "prod", "status": "ok"})
        assert (tmp_path / "cache" / "deploy_history.jsonl").exists()

    def test_append_stores_entry(self, tmp_path: Path) -> None:
        h = _history(tmp_path)
        h.append({"app": "myapp", "host": "prod", "status": "ok"})
        entries = h.read()
        assert len(entries) == 1
        assert entries[0]["app"] == "myapp"

    def test_read_returns_newest_first(self, tmp_path: Path) -> None:
        h = _history(tmp_path)
        h.append({"id": 1, "app": "a"})
        h.append({"id": 2, "app": "a"})
        h.append({"id": 3, "app": "a"})
        entries = h.read()
        assert entries[0]["id"] == 3
        assert entries[-1]["id"] == 1

    def test_read_respects_limit(self, tmp_path: Path) -> None:
        h = _history(tmp_path)
        for i in range(10):
            h.append({"id": i})
        entries = h.read(limit=3)
        assert len(entries) == 3

    def test_filter_by_app(self, tmp_path: Path) -> None:
        h = _history(tmp_path)
        h.append({"app": "alpha", "host": "prod"})
        h.append({"app": "beta", "host": "prod"})
        h.append({"app": "alpha", "host": "staging"})
        entries = h.read(app="alpha")
        assert all(e["app"] == "alpha" for e in entries)
        assert len(entries) == 2

    def test_filter_by_host(self, tmp_path: Path) -> None:
        h = _history(tmp_path)
        h.append({"app": "a", "host": "prod"})
        h.append({"app": "a", "host": "staging"})
        entries = h.read(host="prod")
        assert all(e["host"] == "prod" for e in entries)
        assert len(entries) == 1

    def test_filter_by_app_and_host(self, tmp_path: Path) -> None:
        h = _history(tmp_path)
        h.append({"app": "a", "host": "prod"})
        h.append({"app": "b", "host": "prod"})
        h.append({"app": "a", "host": "staging"})
        entries = h.read(app="a", host="prod")
        assert len(entries) == 1
        assert entries[0]["app"] == "a"
        assert entries[0]["host"] == "prod"

    def test_skips_malformed_json_lines(self, tmp_path: Path) -> None:
        h = _history(tmp_path)
        path = tmp_path / "cache" / "deploy_history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"app":"ok"}\nnot-json\n{"app":"also-ok"}\n', encoding="utf-8")
        entries = h.read()
        assert all(isinstance(e, dict) for e in entries)
        assert len(entries) == 2


class TestDeployHistoryTrim:
    def test_trims_to_keep_limit(self, tmp_path: Path) -> None:
        h = _history(tmp_path, keep=5)
        for i in range(10):
            h.append({"id": i})
        path = tmp_path / "cache" / "deploy_history.jsonl"
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 5

    def test_keeps_newest_entries_after_trim(self, tmp_path: Path) -> None:
        h = _history(tmp_path, keep=3)
        for i in range(6):
            h.append({"id": i})
        entries = h.read(limit=10)
        ids = [e["id"] for e in entries]
        # Should have 3..5 (newest three)
        assert 5 in ids
        assert 4 in ids
        assert 3 in ids
        assert 0 not in ids
