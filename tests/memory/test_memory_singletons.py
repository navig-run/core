"""
tests/test_memory_singletons.py

Thread-safety regression tests for memory module singleton accessors.
Covers: get_memory_manager, get_context_builder, get_snapshot_writer,
        get_links_db, get_knowledge_graph.

Each test fans out 48 concurrent callers via ThreadPoolExecutor and asserts:
  - All callers receive the *same* object (identity, not equality).
  - The constructor / initializer is called exactly once.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKERS = 16
_CALLS = 48


def _collect(fn):
    """Run fn in _WORKERS threads _CALLS times total; return list of results."""
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futs = [pool.submit(fn) for _ in range(_CALLS)]
        return [f.result() for f in as_completed(futs)]


# ---------------------------------------------------------------------------
# MemoryManager
# ---------------------------------------------------------------------------


class TestMemoryManagerSingleton:
    def test_get_memory_manager_threadsafe_singleton(self, tmp_path, monkeypatch):
        """Concurrent callers must receive the same MemoryManager instance."""
        import navig.memory.manager as mod

        init_calls = []

        orig_cls = mod.MemoryManager

        class SlowManager(orig_cls):
            def __init__(self, **kw):
                time.sleep(0.01)
                init_calls.append(1)
                super().__init__(**kw)

        monkeypatch.setattr(mod, "_manager_instance", None)
        monkeypatch.setattr(mod, "MemoryManager", SlowManager)
        monkeypatch.setattr(mod, "_get_memory_dir", lambda: tmp_path / "mem")

        results = _collect(lambda: mod.get_memory_manager(use_embeddings=False))

        assert len(set(id(r) for r in results)) == 1, "Multiple instances created"
        assert sum(init_calls) == 1, f"Constructor called {sum(init_calls)} times, expected 1"

        # cleanup
        monkeypatch.setattr(mod, "_manager_instance", None)

    def test_reload_memory_manager_resets_singleton(self, tmp_path, monkeypatch):
        """reload_memory_manager must replace the singleton safely."""
        import navig.memory.manager as mod

        monkeypatch.setattr(mod, "_manager_instance", None)
        monkeypatch.setattr(mod, "_get_memory_dir", lambda: tmp_path / "mem")

        first = mod.get_memory_manager(use_embeddings=False)
        second = mod.reload_memory_manager()

        assert first is not second, "reload_memory_manager should return a fresh instance"

        # cleanup
        monkeypatch.setattr(mod, "_manager_instance", None)


# ---------------------------------------------------------------------------
# ContextBuilder
# ---------------------------------------------------------------------------


class TestContextBuilderSingleton:
    def test_get_context_builder_threadsafe_singleton(self, monkeypatch):
        """Concurrent callers must receive the same ContextBuilder instance."""
        import navig.memory.context_builder as mod

        init_calls = []

        orig_cls = mod.ContextBuilder

        class SlowBuilder(orig_cls):
            def __init__(self, **kw):
                time.sleep(0.01)
                init_calls.append(1)
                super().__init__(**kw)

        monkeypatch.setattr(mod, "_builder_instance", None)
        monkeypatch.setattr(mod, "ContextBuilder", SlowBuilder)

        results = _collect(mod.get_context_builder)

        assert len(set(id(r) for r in results)) == 1, "Multiple instances created"
        assert sum(init_calls) == 1, f"Constructor called {sum(init_calls)} times, expected 1"

        # cleanup
        monkeypatch.setattr(mod, "_builder_instance", None)

    def test_reset_context_builder_allows_fresh_instance(self, monkeypatch):
        """reset_context_builder must clear the singleton so the next call creates a new one."""
        import navig.memory.context_builder as mod

        monkeypatch.setattr(mod, "_builder_instance", None)

        first = mod.get_context_builder()
        mod.reset_context_builder()
        second = mod.get_context_builder()

        assert first is not second

        # cleanup
        monkeypatch.setattr(mod, "_builder_instance", None)


# ---------------------------------------------------------------------------
# SnapshotWriter
# ---------------------------------------------------------------------------


class TestSnapshotWriterSingleton:
    def test_get_snapshot_writer_threadsafe_singleton(self, monkeypatch):
        """Concurrent callers must receive the same SnapshotWriter instance."""
        import navig.memory.snapshot as mod

        init_calls = []

        orig_cls = mod.SnapshotWriter

        class SlowWriter(orig_cls):
            def __init__(self, **kw):
                time.sleep(0.01)
                init_calls.append(1)
                super().__init__(**kw)

        monkeypatch.setattr(mod, "_writer", None)
        monkeypatch.setattr(mod, "SnapshotWriter", SlowWriter)

        results = _collect(mod.get_snapshot_writer)

        assert len(set(id(r) for r in results)) == 1, "Multiple instances created"
        assert sum(init_calls) == 1, f"Constructor called {sum(init_calls)} times, expected 1"

        # cleanup
        mod.reset_snapshot_writer()

    def test_reset_snapshot_writer_allows_fresh_instance(self, monkeypatch):
        """reset_snapshot_writer must clear the singleton."""
        import navig.memory.snapshot as mod

        monkeypatch.setattr(mod, "_writer", None)

        first = mod.get_snapshot_writer()
        mod.reset_snapshot_writer()
        second = mod.get_snapshot_writer()

        assert first is not second

        # cleanup
        mod.reset_snapshot_writer()


# ---------------------------------------------------------------------------
# LinksDB
# ---------------------------------------------------------------------------


class TestLinksDbSingleton:
    def test_get_links_db_threadsafe_singleton(self, tmp_path, monkeypatch):
        """Concurrent callers must receive the same LinksDB instance."""
        import navig.memory.links_db as mod

        init_calls = []

        orig_cls = mod.LinksDB
        db_path = tmp_path / "links.db"

        class SlowLinksDB(orig_cls):
            def __init__(self, path):
                time.sleep(0.01)
                init_calls.append(1)
                super().__init__(path)

        monkeypatch.setattr(mod, "_db_instance", None)
        monkeypatch.setattr(mod, "LinksDB", SlowLinksDB)

        # Patch get_config so no real config is needed
        fake_cfg = type("Cfg", (), {"data_dir": str(tmp_path)})()
        monkeypatch.setattr("navig.config.get_config", lambda: fake_cfg, raising=False)

        results = _collect(mod.get_links_db)

        assert len(set(id(r) for r in results)) == 1, "Multiple instances created"
        assert sum(init_calls) == 1, f"Constructor called {sum(init_calls)} times, expected 1"

        # cleanup
        mod.reset_links_db()

    def test_reset_links_db_allows_fresh_instance(self, tmp_path, monkeypatch):
        """reset_links_db must close and clear the singleton."""
        import navig.memory.links_db as mod

        monkeypatch.setattr(mod, "_db_instance", None)

        fake_cfg = type("Cfg", (), {"data_dir": str(tmp_path)})()
        monkeypatch.setattr("navig.config.get_config", lambda: fake_cfg, raising=False)

        first = mod.get_links_db()
        mod.reset_links_db()
        second = mod.get_links_db()

        assert first is not second

        # cleanup
        mod.reset_links_db()


# ---------------------------------------------------------------------------
# KnowledgeGraph
# ---------------------------------------------------------------------------


class TestKnowledgeGraphSingleton:
    def test_get_knowledge_graph_threadsafe_singleton(self, tmp_path, monkeypatch):
        """Concurrent callers must receive the same KnowledgeGraph instance."""
        import navig.memory.knowledge_graph as mod

        init_calls = []

        orig_cls = mod.KnowledgeGraph
        db_path = tmp_path / "kg.db"

        class SlowKG(orig_cls):
            def __init__(self, path):
                time.sleep(0.01)
                init_calls.append(1)
                super().__init__(path)

        monkeypatch.setattr(mod, "_kg_instance", None)
        monkeypatch.setattr(mod, "KnowledgeGraph", SlowKG)

        fake_cfg = type("Cfg", (), {"data_dir": str(tmp_path)})()
        monkeypatch.setattr("navig.config.get_config", lambda: fake_cfg, raising=False)

        results = _collect(mod.get_knowledge_graph)

        assert len(set(id(r) for r in results)) == 1, "Multiple instances created"
        assert sum(init_calls) == 1, f"Constructor called {sum(init_calls)} times, expected 1"

        # cleanup
        mod.reset_knowledge_graph()

    def test_reset_knowledge_graph_allows_fresh_instance(self, tmp_path, monkeypatch):
        """reset_knowledge_graph must close and clear the singleton."""
        import navig.memory.knowledge_graph as mod

        monkeypatch.setattr(mod, "_kg_instance", None)

        fake_cfg = type("Cfg", (), {"data_dir": str(tmp_path)})()
        monkeypatch.setattr("navig.config.get_config", lambda: fake_cfg, raising=False)

        first = mod.get_knowledge_graph()
        mod.reset_knowledge_graph()
        second = mod.get_knowledge_graph()

        assert first is not second

        # cleanup
        mod.reset_knowledge_graph()
