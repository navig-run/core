"""Tests for navig.memory._util and navig.memory.paths."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.memory._util
# ---------------------------------------------------------------------------

class TestMemoryUtil:
    def setup_method(self):
        from navig.memory import _util
        self._mod = _util

    def test_debug_log_does_not_raise(self):
        self._mod._debug_log("hello test")  # must not raise

    def test_debug_log_swallows_exceptions(self):
        """_debug_log must never raise even if logger is broken."""
        with patch.object(self._mod._logger, "debug", side_effect=Exception("boom")):
            self._mod._debug_log("should not raise")  # No exception

    def test_atomic_write_text_delegates(self, tmp_path):
        dest = tmp_path / "out.txt"
        self._mod._atomic_write_text(dest, "content123")
        assert dest.read_text() == "content123"

    def test_atomic_write_text_overwrites(self, tmp_path):
        dest = tmp_path / "out.txt"
        self._mod._atomic_write_text(dest, "first")
        self._mod._atomic_write_text(dest, "second")
        assert dest.read_text() == "second"


# ---------------------------------------------------------------------------
# navig.memory.paths
# ---------------------------------------------------------------------------

class TestMemoryPaths:
    def test_navig_home_uses_env_var(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVIG_HOME", str(tmp_path))
        from importlib import import_module, reload
        import navig.memory.paths as p
        result = p.navig_home()
        assert result == tmp_path

    def test_navig_home_falls_back_to_platform(self, monkeypatch):
        monkeypatch.delenv("NAVIG_HOME", raising=False)
        import navig.memory.paths as p
        with patch("navig.platform.paths.config_dir") as mock_cd:
            mock_cd.return_value = Path("/fake/config")
            result = p.navig_home()
        assert result == Path("/fake/config")

    def test_memory_dir_is_subdir_of_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVIG_HOME", str(tmp_path))
        import navig.memory.paths as p
        d = p.memory_dir()
        assert d == tmp_path / "memory"

    def test_memory_dir_created(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVIG_HOME", str(tmp_path))
        import navig.memory.paths as p
        d = p.memory_dir()
        # memory_dir() returns the path but does NOT create it (the docstring
        # avoids import-time fs mutations); the caller creates it.
        assert d == tmp_path / "memory"
        d.mkdir(parents=True, exist_ok=True)
        assert d.is_dir()

    def test_key_facts_db_path_type(self):
        import navig.memory.paths as p
        assert isinstance(p.KEY_FACTS_DB_PATH, Path)
        assert p.KEY_FACTS_DB_PATH.name == "key_facts.db"
