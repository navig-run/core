"""
Batch 76: hermetic unit tests for
  - navig/core/crash_handler.py  (CrashHandler methods)
  - navig/core/safe_eval.py      (list, tuple, dict, subscript additional coverage)
  - navig/core/shared_config.py  (_get_nested / _set_nested helpers)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/core/crash_handler.py
# ---------------------------------------------------------------------------

class TestCrashHandlerDebugMode:
    def test_is_debug_false_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_DEBUG", raising=False)
        from navig.core.crash_handler import CrashHandler
        h = CrashHandler()
        assert h.is_debug is False

    def test_is_debug_true_when_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NAVIG_DEBUG", "1")
        from navig.core.crash_handler import CrashHandler
        h = CrashHandler()
        assert h.is_debug is True

    def test_enable_debug_flips_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_DEBUG", raising=False)
        from navig.core.crash_handler import CrashHandler
        h = CrashHandler()
        assert h.is_debug is False
        h.enable_debug()
        assert h.is_debug is True


class TestCrashHandlerLogToFile:
    def test_writes_json_log_with_expected_fields(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_DEBUG", raising=False)
        from navig.core.crash_handler import CrashHandler
        h = CrashHandler()
        h._log_dir = tmp_path  # bypass config_dir
        exc = ValueError("test error")
        log_path = h._log_crash_to_file(exc)
        assert log_path is not None and log_path.exists()
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert data["exception_type"] == "ValueError"
        assert "test error" in data["exception_message"]
        assert "timestamp" in data
        assert "traceback" in data
        assert "system" in data

    def test_returns_none_when_log_write_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_DEBUG", raising=False)
        from navig.core.crash_handler import CrashHandler
        h = CrashHandler()
        # Force atomic_write_text to raise
        with patch("navig.core.yaml_io.atomic_write_text", side_effect=OSError("disk full")):
            result = h._log_crash_to_file(RuntimeError("boom"))
        assert result is None


class TestCrashHandlerCleanup:
    def test_removes_oldest_when_over_limit(self, tmp_path: Path) -> None:
        from navig.core.crash_handler import CrashHandler, _MAX_CRASH_LOGS
        h = CrashHandler()
        # Create _MAX_CRASH_LOGS + 3 files
        logs = []
        for i in range(_MAX_CRASH_LOGS + 3):
            f = tmp_path / f"crash-{i:05d}.json"
            f.write_text("{}")
            logs.append(f)
        # They are sorted by mtime; make timestamps distinct enough
        import time
        for idx, f in enumerate(logs):
            import os
            os.utime(f, (idx, idx))
        h._cleanup_old_logs(tmp_path)
        remaining = list(tmp_path.glob("crash-*.json"))
        assert len(remaining) == _MAX_CRASH_LOGS

    def test_cleanup_does_not_raise_on_empty_dir(self, tmp_path: Path) -> None:
        from navig.core.crash_handler import CrashHandler
        h = CrashHandler()
        h._cleanup_old_logs(tmp_path)  # no files, no error


class TestCrashHandlerGetLatestReport:
    def test_returns_none_when_no_logs(self, tmp_path: Path) -> None:
        from navig.core.crash_handler import CrashHandler
        h = CrashHandler()
        h._log_dir = tmp_path
        assert h.get_latest_crash_report() is None

    def test_returns_most_recent_log(self, tmp_path: Path) -> None:
        from navig.core.crash_handler import CrashHandler
        import os
        h = CrashHandler()
        h._log_dir = tmp_path
        older = tmp_path / "crash-00001.json"
        newer = tmp_path / "crash-00002.json"
        older.write_text(json.dumps({"id": "old"}))
        newer.write_text(json.dumps({"id": "new"}))
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))
        result = h.get_latest_crash_report()
        assert result is not None
        assert result["id"] == "new"


# ---------------------------------------------------------------------------
# navig/core/safe_eval.py — additional coverage
# ---------------------------------------------------------------------------

class TestSafeEvalContainers:
    def test_list_literal(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("[1, 2, 3]") == [1, 2, 3]

    def test_tuple_literal(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("(10, 20)") == (10, 20)

    def test_dict_literal(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval('{"a": 1}') == {"a": 1}

    def test_nested_list(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("[[1, 2], [3, 4]]") == [[1, 2], [3, 4]]

    def test_subscript_list(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("[10, 20, 30][1]") == 20

    def test_subscript_dict(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval('{"x": 99}["x"]') == 99

    def test_variable_subscript(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("items[0]", {"items": ["first", "second"]}) == "first"

    def test_unknown_variable_raises(self) -> None:
        from navig.core.safe_eval import safe_eval
        with pytest.raises(ValueError, match="Unknown variable"):
            safe_eval("undefined_var")


# ---------------------------------------------------------------------------
# navig/core/shared_config.py — _get_nested / _set_nested helpers
# ---------------------------------------------------------------------------

class TestConfigSingletonHelpers:
    """Test _get_nested and _set_nested via a fresh instance (bypassing singleton)."""

    def _make_instance(self):
        """Create an uninitialized ConfigSingleton-like object to unit-test helpers."""
        from navig.core.shared_config import ConfigSingleton
        obj = object.__new__(ConfigSingleton)
        return obj

    def test_get_nested_simple_key(self) -> None:
        from navig.core.shared_config import ConfigSingleton
        obj = self._make_instance()
        data = {"host": "example.com"}
        assert obj._get_nested(data, "host") == "example.com"

    def test_get_nested_dot_notation(self) -> None:
        from navig.core.shared_config import ConfigSingleton
        obj = self._make_instance()
        data = {"plugins": {"brain": {"db_path": "/tmp/brain.db"}}}
        assert obj._get_nested(data, "plugins.brain.db_path") == "/tmp/brain.db"

    def test_get_nested_missing_returns_default(self) -> None:
        from navig.core.shared_config import ConfigSingleton
        obj = self._make_instance()
        data = {"plugins": {}}
        assert obj._get_nested(data, "plugins.missing.key", "fallback") == "fallback"

    def test_get_nested_missing_no_default_returns_none(self) -> None:
        from navig.core.shared_config import ConfigSingleton
        obj = self._make_instance()
        assert obj._get_nested({}, "no.such.key") is None

    def test_set_nested_simple_key(self) -> None:
        from navig.core.shared_config import ConfigSingleton
        obj = self._make_instance()
        data: dict = {}
        obj._set_nested(data, "host", "example.com")
        assert data == {"host": "example.com"}

    def test_set_nested_dot_notation_creates_parents(self) -> None:
        from navig.core.shared_config import ConfigSingleton
        obj = self._make_instance()
        data: dict = {}
        obj._set_nested(data, "plugins.brain.db_path", "/tmp/test.db")
        assert data == {"plugins": {"brain": {"db_path": "/tmp/test.db"}}}

    def test_set_nested_overwrites_leaf(self) -> None:
        from navig.core.shared_config import ConfigSingleton
        obj = self._make_instance()
        data = {"a": {"b": "old"}}
        obj._set_nested(data, "a.b", "new")
        assert data["a"]["b"] == "new"
