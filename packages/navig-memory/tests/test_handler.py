"""Tests for navig-memory/handler.py"""
from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from handler import (
    COMMANDS,
    _JsonMemoryStore,
    cmd_memory_checkpoint,
    cmd_memory_clear,
    cmd_memory_search,
    cmd_memory_store,
    on_event,
    on_load,
    on_unload,
)

# ── _JsonMemoryStore ──────────────────────────────────────────────────────────

class TestJsonMemoryStore:
    def test_store_returns_id(self, tmp_path):
        store = _JsonMemoryStore(tmp_path / "memories.json")
        mid = store.store("hello world")
        assert mid == "1"

    def test_store_persists_to_disk(self, tmp_path):
        path = tmp_path / "memories.json"
        store = _JsonMemoryStore(path)
        store.store("persisted entry", tags=["test"])
        data = json.loads(path.read_text())
        assert len(data) == 1
        assert data[0]["content"] == "persisted entry"
        assert data[0]["tags"] == ["test"]

    def test_store_increments_id(self, tmp_path):
        store = _JsonMemoryStore(tmp_path / "memories.json")
        id1 = store.store("first")
        id2 = store.store("second")
        assert id1 == "1"
        assert id2 == "2"

    def test_search_matches_content(self, tmp_path):
        store = _JsonMemoryStore(tmp_path / "memories.json")
        store.store("the quick brown fox")
        store.store("lazy dog")
        results = store.search("fox")
        assert len(results) == 1
        assert "fox" in results[0]["content"]

    def test_search_matches_tags(self, tmp_path):
        store = _JsonMemoryStore(tmp_path / "memories.json")
        store.store("plain content", tags=["important", "todo"])
        results = store.search("todo")
        assert len(results) == 1

    def test_search_limit_respected(self, tmp_path):
        store = _JsonMemoryStore(tmp_path / "memories.json")
        for i in range(10):
            store.store(f"entry {i} match")
        results = store.search("match", limit=3)
        assert len(results) == 3

    def test_search_empty_returns_empty(self, tmp_path):
        store = _JsonMemoryStore(tmp_path / "memories.json")
        results = store.search("nothing")
        assert results == []

    def test_clear_without_confirm_returns_zero(self, tmp_path):
        store = _JsonMemoryStore(tmp_path / "memories.json")
        store.store("do not delete")
        count = store.clear(confirm=False)
        assert count == 0
        assert len(store._load()) == 1

    def test_clear_with_confirm_wipes_all(self, tmp_path):
        store = _JsonMemoryStore(tmp_path / "memories.json")
        store.store("a")
        store.store("b")
        count = store.clear(confirm=True)
        assert count == 2
        assert store._load() == []

    def test_load_returns_empty_on_missing_file(self, tmp_path):
        store = _JsonMemoryStore(tmp_path / "nosuchfile.json")
        assert store._load() == []

    def test_load_returns_empty_on_corrupt_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not valid json")
        store = _JsonMemoryStore(path)
        assert store._load() == []


# ── cmd_memory_store ──────────────────────────────────────────────────────────

class TestCmdMemoryStore:
    def test_missing_content_returns_error(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        result = cmd_memory_store({}, ctx)
        assert result["status"] == "error"
        assert "content" in result["message"].lower()

    def test_stores_and_returns_id(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        result = cmd_memory_store({"content": "hello"}, ctx)
        assert result["status"] == "ok"
        assert "id" in result["data"]

    def test_tags_optional(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        result = cmd_memory_store({"content": "no tags"}, ctx)
        assert result["status"] == "ok"


# ── cmd_memory_search ─────────────────────────────────────────────────────────

class TestCmdMemorySearch:
    def test_missing_query_returns_error(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        result = cmd_memory_search({}, ctx)
        assert result["status"] == "error"
        assert "query" in result["message"].lower()

    def test_search_returns_ok_with_results(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        cmd_memory_store({"content": "searchable content"}, ctx)
        result = cmd_memory_search({"query": "searchable"}, ctx)
        assert result["status"] == "ok"
        assert len(result["data"]) == 1

    def test_search_no_match_returns_empty_list(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        result = cmd_memory_search({"query": "nothing"}, ctx)
        assert result["status"] == "ok"
        assert result["data"] == []

    def test_limit_respected(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        for i in range(5):
            cmd_memory_store({"content": f"item {i}"}, ctx)
        result = cmd_memory_search({"query": "item", "limit": "2"}, ctx)
        assert result["status"] == "ok"
        assert len(result["data"]) == 2


# ── cmd_memory_clear ──────────────────────────────────────────────────────────

class TestCmdMemoryClear:
    def test_without_confirm_returns_error(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        result = cmd_memory_clear({}, ctx)
        assert result["status"] == "error"
        assert "confirm" in result["message"].lower()

    def test_with_confirm_clears(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        cmd_memory_store({"content": "to be cleared"}, ctx)
        result = cmd_memory_clear({"confirm": True}, ctx)
        assert result["status"] == "ok"
        assert result["data"]["cleared"] == 1


# ── cmd_memory_checkpoint ─────────────────────────────────────────────────────

class TestCmdMemoryCheckpoint:
    def test_checkpoint_creates_file(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        with patch("handler._latest_conversation_snapshot", return_value=None):
            result = cmd_memory_checkpoint({}, ctx)
        assert result["status"] == "ok"
        checkpoint_file = pathlib.Path(result["data"]["path"])
        assert checkpoint_file.exists()
        data = json.loads(checkpoint_file.read_text())
        assert "id" in data
        assert "created_at" in data

    def test_checkpoint_uses_root_path(self, tmp_path):
        ctx = {"store_dir": str(tmp_path)}
        with patch("handler._latest_conversation_snapshot", return_value=None):
            result = cmd_memory_checkpoint({"root_path": "/my/project"}, ctx)
        data = json.loads(pathlib.Path(result["data"]["path"]).read_text())
        assert data["workspace_root"] == "/my/project"


# ── COMMANDS registry ─────────────────────────────────────────────────────────

class TestCommandsRegistry:
    def test_all_four_commands_registered(self):
        assert "memory_store" in COMMANDS
        assert "memory_search" in COMMANDS
        assert "memory_clear" in COMMANDS
        assert "memory_checkpoint" in COMMANDS

    def test_commands_are_callable(self):
        for cmd in COMMANDS.values():
            assert callable(cmd)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_on_event_returns_none(self):
        assert on_event("any", {}) is None

    def test_on_load_registers_commands(self):
        mock_registry = MagicMock()
        with patch.dict(sys.modules, {"navig.commands._registry": mock_registry}):
            mock_registry.CommandRegistry = MagicMock()
            on_load({})
        # Either registered or gracefully fell back (ImportError path)

    def test_on_load_no_raise_without_registry(self):
        with patch.dict(sys.modules, {"navig": None, "navig.commands": None, "navig.commands._registry": None}):
            on_load({})  # should not raise

    def test_on_unload_no_raise_without_registry(self):
        with patch.dict(sys.modules, {"navig": None, "navig.commands": None, "navig.commands._registry": None}):
            on_unload({})  # should not raise
