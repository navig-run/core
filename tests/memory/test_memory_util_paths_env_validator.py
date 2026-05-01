"""
Batch 82: navig/memory/_util.py, navig/memory/paths.py,
          navig/env_validator.py
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# memory/_util.py
# ---------------------------------------------------------------------------
from navig.memory._util import _debug_log, _atomic_write_text


class TestDebugLog:
    def test_does_not_raise(self):
        # Should never raise regardless of message
        _debug_log("test message")
        _debug_log("")
        _debug_log("unicode: 日本語")

    def test_with_various_messages(self):
        # Just confirm it handles diverse inputs without error
        for msg in ["short", "a" * 500, "special \n\t chars"]:
            _debug_log(msg)


class TestAtomicWriteTextShim:
    def test_writes_file(self, tmp_path):
        f = tmp_path / "test.txt"
        _atomic_write_text(f, "hello world")
        assert f.read_text() == "hello world"

    def test_overwrites_file(self, tmp_path):
        f = tmp_path / "test.txt"
        _atomic_write_text(f, "first")
        _atomic_write_text(f, "second")
        assert f.read_text() == "second"


# ---------------------------------------------------------------------------
# memory/paths.py
# ---------------------------------------------------------------------------
from navig.memory.paths import navig_home, memory_dir


class TestNavigHome:
    def test_respects_navig_home_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_HOME", str(tmp_path))
        result = navig_home()
        assert result == tmp_path

    def test_falls_back_to_config_dir(self, monkeypatch):
        monkeypatch.delenv("NAVIG_HOME", raising=False)
        result = navig_home()
        assert isinstance(result, Path)
        assert result.name  # non-empty path

    def test_returns_path_object(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_HOME", str(tmp_path))
        result = navig_home()
        assert isinstance(result, Path)


class TestMemoryDir:
    def test_returns_memory_subdir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_HOME", str(tmp_path))
        result = memory_dir()
        assert result.name == "memory"
        assert result.parent == tmp_path

    def test_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_HOME", str(tmp_path))
        result = memory_dir()
        assert result.exists()
        assert result.is_dir()


# ---------------------------------------------------------------------------
# env_validator.py
# ---------------------------------------------------------------------------
from navig.env_validator import validate_environment


class TestValidateEnvironment:
    def test_passes_with_openrouter_key(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Should not raise
        validate_environment()

    def test_passes_with_openai_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        validate_environment()

    def test_passes_with_anthropic_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        validate_environment()

    def test_raises_without_any_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="missing"):
            validate_environment()

    def test_raises_prints_to_stderr(self, monkeypatch, capsys):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError):
            validate_environment()
        captured = capsys.readouterr()
        assert "Environment Verification Failed" in captured.err or "Missing" in captured.err
