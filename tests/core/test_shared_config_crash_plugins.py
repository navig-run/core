"""
Tests for:
  - navig.core.shared_config  (_get_nested, _set_nested, _get_default_config, plugin helpers)
  - navig.core.crash_handler  (CrashHandler lifecycle, log cleanup, latest report)
  - navig.core.protocols      (Protocol structural conformance)
  - navig.core.plugins        (PluginState, PluginType, PluginMetadata, PluginInfo)

All tests are hermetic — no real SSH, config files, or network calls.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_crash_handler(tmp_path: Path):
    """Return a CrashHandler with log_dir pointing to tmp_path/logs."""
    from navig.core.crash_handler import CrashHandler

    ch = CrashHandler()
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ch._log_dir = log_dir  # bypass get_config_manager
    return ch


# ===========================================================================
# navig.core.shared_config — pure internal helpers (no singleton I/O)
# ===========================================================================

class TestGetNested:
    """_get_nested resolves dot-notation paths."""

    def _cfg(self):
        from navig.core.shared_config import ConfigSingleton
        return ConfigSingleton.__new__(ConfigSingleton)  # raw object, no __init__

    def test_simple_key(self):
        obj = self._cfg()
        data = {"foo": "bar"}
        assert obj._get_nested(data, "foo") == "bar"

    def test_nested_two_levels(self):
        obj = self._cfg()
        data = {"a": {"b": 42}}
        assert obj._get_nested(data, "a.b") == 42

    def test_nested_three_levels(self):
        obj = self._cfg()
        data = {"x": {"y": {"z": "deep"}}}
        assert obj._get_nested(data, "x.y.z") == "deep"

    def test_missing_key_returns_default(self):
        obj = self._cfg()
        assert obj._get_nested({}, "missing", "fallback") == "fallback"

    def test_missing_middle_key_returns_default(self):
        obj = self._cfg()
        data = {"a": {"b": 1}}
        assert obj._get_nested(data, "a.c.d", "nope") == "nope"

    def test_non_dict_intermediate_returns_default(self):
        obj = self._cfg()
        data = {"a": "string"}
        assert obj._get_nested(data, "a.b") is None

    def test_none_value_returns_default(self):
        obj = self._cfg()
        data = {"key": None}
        assert obj._get_nested(data, "key", "default") == "default"

    def test_false_value_is_returned(self):
        """False is not None; should be returned, not overridden by default."""
        obj = self._cfg()
        # False will be returned because the loop ends on a non-None value.
        # (But _get_nested returns default when value IS None, not when falsy.)
        data = {"key": False}
        # The implementation returns `value if value is not None else default`
        # so False should be returned.
        result = obj._get_nested(data, "key", "default")
        assert result is False


class TestSetNested:
    """_set_nested writes dot-notation paths, creating intermediate dicts."""

    def _cfg(self):
        from navig.core.shared_config import ConfigSingleton
        return ConfigSingleton.__new__(ConfigSingleton)

    def test_simple_key(self):
        obj = self._cfg()
        data = {}
        obj._set_nested(data, "foo", "bar")
        assert data["foo"] == "bar"

    def test_nested_two_levels(self):
        obj = self._cfg()
        data = {}
        obj._set_nested(data, "a.b", 99)
        assert data["a"]["b"] == 99

    def test_nested_creates_intermediate(self):
        obj = self._cfg()
        data = {}
        obj._set_nested(data, "x.y.z", "deep")
        assert data["x"]["y"]["z"] == "deep"

    def test_overwrite_existing(self):
        obj = self._cfg()
        data = {"a": {"b": 1}}
        obj._set_nested(data, "a.b", 2)
        assert data["a"]["b"] == 2

    def test_overwrite_non_dict_intermediate(self):
        """If intermediate is not a dict, it is replaced with a new dict."""
        obj = self._cfg()
        data = {"a": "not_a_dict"}
        obj._set_nested(data, "a.b", 5)
        assert data["a"]["b"] == 5


class TestGetDefaultConfig:
    """_get_default_config returns required scaffolding keys."""

    def test_returns_dict(self):
        from navig.core.shared_config import ConfigSingleton

        obj = ConfigSingleton.__new__(ConfigSingleton)
        obj.global_config_dir = Path("/fake")
        cfg = obj._get_default_config()
        assert isinstance(cfg, dict)

    def test_has_active_host_key(self):
        from navig.core.shared_config import ConfigSingleton

        obj = ConfigSingleton.__new__(ConfigSingleton)
        obj.global_config_dir = Path("/fake")
        cfg = obj._get_default_config()
        assert "active_host" in cfg

    def test_has_execution_dict(self):
        from navig.core.shared_config import ConfigSingleton

        obj = ConfigSingleton.__new__(ConfigSingleton)
        obj.global_config_dir = Path("/fake")
        cfg = obj._get_default_config()
        assert "execution" in cfg
        assert isinstance(cfg["execution"], dict)

    def test_has_plugins_dict(self):
        from navig.core.shared_config import ConfigSingleton

        obj = ConfigSingleton.__new__(ConfigSingleton)
        obj.global_config_dir = Path("/fake")
        cfg = obj._get_default_config()
        assert "plugins" in cfg
        assert cfg["plugins"]["enabled"] is True

    def test_debug_log_false_by_default(self):
        from navig.core.shared_config import ConfigSingleton

        obj = ConfigSingleton.__new__(ConfigSingleton)
        obj.global_config_dir = Path("/fake")
        cfg = obj._get_default_config()
        assert cfg["debug_log"] is False


# ===========================================================================
# navig.core.crash_handler
# ===========================================================================

class TestCrashHandlerDebugMode:
    def test_default_debug_false(self):
        from navig.core.crash_handler import CrashHandler

        with patch.dict(os.environ, {"NAVIG_DEBUG": "0"}):
            ch = CrashHandler()
            assert ch.is_debug is False

    def test_env_debug_true(self):
        from navig.core.crash_handler import CrashHandler

        with patch.dict(os.environ, {"NAVIG_DEBUG": "1"}):
            ch = CrashHandler()
            assert ch.is_debug is True

    def test_enable_debug_programmatic(self):
        from navig.core.crash_handler import CrashHandler

        with patch.dict(os.environ, {"NAVIG_DEBUG": "0"}):
            ch = CrashHandler()
            ch.enable_debug()
            assert ch.is_debug is True

    def test_enable_debug_sets_env_var(self):
        from navig.core.crash_handler import CrashHandler

        with patch.dict(os.environ, {"NAVIG_DEBUG": "0"}, clear=False):
            ch = CrashHandler()
            ch.enable_debug()
            assert os.environ.get("NAVIG_DEBUG") == "1"


class TestCrashHandlerLogCleanup:
    def test_cleanup_removes_oldest_beyond_max(self, tmp_path):
        ch = _make_crash_handler(tmp_path)
        log_dir = ch._log_dir

        # Create 15 crash log files with distinct mtime
        for i in range(15):
            p = log_dir / f"crash-20240101-{i:06d}.json"
            p.write_text("{}", encoding="utf-8")

        ch._cleanup_old_logs(log_dir)
        remaining = list(log_dir.glob("crash-*.json"))
        assert len(remaining) == 10  # _MAX_CRASH_LOGS

    def test_cleanup_no_error_on_empty_dir(self, tmp_path):
        ch = _make_crash_handler(tmp_path)
        # Should not raise
        ch._cleanup_old_logs(ch._log_dir)

    def test_cleanup_keeps_within_limit(self, tmp_path):
        ch = _make_crash_handler(tmp_path)
        log_dir = ch._log_dir

        for i in range(5):
            p = log_dir / f"crash-20240101-{i:06d}.json"
            p.write_text("{}", encoding="utf-8")

        ch._cleanup_old_logs(log_dir)
        remaining = list(log_dir.glob("crash-*.json"))
        assert len(remaining) == 5  # none removed


class TestGetLatestCrashReport:
    def test_returns_none_when_no_logs(self, tmp_path):
        ch = _make_crash_handler(tmp_path)
        result = ch.get_latest_crash_report()
        assert result is None

    def test_returns_parsed_json(self, tmp_path):
        ch = _make_crash_handler(tmp_path)
        report = {"exception_type": "ValueError", "exception_message": "test"}
        crash_file = ch._log_dir / "crash-20240101-120000.json"
        crash_file.write_text(json.dumps(report), encoding="utf-8")

        result = ch.get_latest_crash_report()
        assert result is not None
        assert result["exception_type"] == "ValueError"

    def test_returns_most_recent_by_mtime(self, tmp_path):
        ch = _make_crash_handler(tmp_path)
        log_dir = ch._log_dir

        old = log_dir / "crash-20240101-000000.json"
        old.write_text(json.dumps({"exception_type": "OldError"}), encoding="utf-8")
        new = log_dir / "crash-20240201-000000.json"
        new.write_text(json.dumps({"exception_type": "NewError"}), encoding="utf-8")
        # Touch new file to ensure it has a newer mtime
        new.touch()

        result = ch.get_latest_crash_report()
        assert result["exception_type"] == "NewError"


class TestLogCrashToFile:
    def test_writes_json_file(self, tmp_path):
        ch = _make_crash_handler(tmp_path)
        exc = ValueError("something went wrong")
        log_path = ch._log_crash_to_file(exc)

        assert log_path is not None
        assert log_path.exists()
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert data["exception_type"] == "ValueError"
        assert "something went wrong" in data["exception_message"]
        assert "traceback" in data
        assert "system" in data

    def test_contains_system_keys(self, tmp_path):
        ch = _make_crash_handler(tmp_path)
        log_path = ch._log_crash_to_file(RuntimeError("boom"))
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert "platform" in data["system"]
        assert "python" in data["system"]


# ===========================================================================
# navig.core.protocols — structural checks
# ===========================================================================

class TestConfigProviderProtocol:
    """Verify the Protocol itself is importable and describes required attrs."""

    def test_importable(self):
        from navig.core.protocols import ConfigProvider  # noqa: F401

    def test_app_config_dir_in_protocol(self):
        import inspect
        from navig.core.protocols import ConfigProvider

        # Protocol members should include app_config_dir
        members = list(ConfigProvider.__protocol_attrs__)
        assert "app_config_dir" in members

    def test_global_config_dir_in_protocol(self):
        import inspect
        from navig.core.protocols import ConfigProvider

        members = list(ConfigProvider.__protocol_attrs__)
        assert "global_config_dir" in members

    def test_host_config_provider_extends_config(self):
        from navig.core.protocols import ConfigProvider, HostConfigProvider

        # HostConfigProvider inherits from ConfigProvider at the class level
        assert ConfigProvider in HostConfigProvider.__mro__

    def test_app_config_provider_has_list_hosts(self):
        from navig.core.protocols import AppConfigProvider

        members = list(AppConfigProvider.__protocol_attrs__)
        assert "list_hosts" in members


# ===========================================================================
# navig.core.plugins — data types
# ===========================================================================

class TestPluginState:
    def test_all_states_present(self):
        from navig.core.plugins import PluginState

        names = {s.name for s in PluginState}
        expected = {"DISCOVERED", "LOADED", "ENABLED", "DISABLED", "ERROR", "UNLOADED"}
        assert expected == names

    def test_state_is_string_enum(self):
        from navig.core.plugins import PluginState

        assert PluginState.ENABLED.value == "enabled"
        assert PluginState.ERROR.value == "error"

    def test_constructible_from_value(self):
        from navig.core.plugins import PluginState

        assert PluginState("discovered") == PluginState.DISCOVERED


class TestPluginType:
    def test_expected_types(self):
        from navig.core.plugins import PluginType

        names = {t.name for t in PluginType}
        expected = {"COMMAND", "CHANNEL", "PROVIDER", "TOOL", "HOOK", "EXTENSION"}
        assert expected == names

    def test_values_are_lowercase(self):
        from navig.core.plugins import PluginType

        for pt in PluginType:
            assert pt.value == pt.value.lower()


class TestPluginMetadata:
    def test_minimal_construction(self):
        from navig.core.plugins import PluginMetadata

        meta = PluginMetadata(name="test-plugin", version="1.0.0")
        assert meta.name == "test-plugin"
        assert meta.version == "1.0.0"

    def test_defaults(self):
        from navig.core.plugins import PluginMetadata, PluginType

        meta = PluginMetadata(name="x", version="0.1")
        assert meta.description == ""
        assert meta.type == PluginType.EXTENSION
        assert meta.auto_enable is True
        assert meta.priority == 100
        assert meta.tags == []
        assert meta.dependencies == []

    def test_to_dict_shape(self):
        from navig.core.plugins import PluginMetadata, PluginType

        meta = PluginMetadata(name="myplugin", version="2.0.0", description="great")
        d = meta.to_dict()
        assert d["name"] == "myplugin"
        assert d["version"] == "2.0.0"
        assert d["description"] == "great"
        assert "type" in d
        assert "tags" in d
        assert "dependencies" in d

    def test_to_dict_type_is_string_value(self):
        from navig.core.plugins import PluginMetadata, PluginType

        meta = PluginMetadata(name="x", version="1", type=PluginType.COMMAND)
        d = meta.to_dict()
        assert d["type"] == "command"


class TestPluginInfo:
    def _meta(self):
        from navig.core.plugins import PluginMetadata

        return PluginMetadata(name="test", version="1.0")

    def test_default_state_is_discovered(self):
        from navig.core.plugins import PluginInfo, PluginState

        info = PluginInfo(metadata=self._meta())
        assert info.state == PluginState.DISCOVERED

    def test_is_enabled_true_when_enabled(self):
        from navig.core.plugins import PluginInfo, PluginState

        info = PluginInfo(metadata=self._meta(), state=PluginState.ENABLED)
        assert info.is_enabled() is True

    def test_is_enabled_false_when_loaded(self):
        from navig.core.plugins import PluginInfo, PluginState

        info = PluginInfo(metadata=self._meta(), state=PluginState.LOADED)
        assert info.is_enabled() is False

    def test_is_loaded_true_for_enabled_state(self):
        from navig.core.plugins import PluginInfo, PluginState

        info = PluginInfo(metadata=self._meta(), state=PluginState.ENABLED)
        assert info.is_loaded() is True

    def test_is_loaded_true_for_disabled_state(self):
        from navig.core.plugins import PluginInfo, PluginState

        info = PluginInfo(metadata=self._meta(), state=PluginState.DISABLED)
        assert info.is_loaded() is True

    def test_is_loaded_false_for_discovered(self):
        from navig.core.plugins import PluginInfo, PluginState

        info = PluginInfo(metadata=self._meta(), state=PluginState.DISCOVERED)
        assert info.is_loaded() is False

    def test_name_delegates_to_metadata(self):
        from navig.core.plugins import PluginInfo

        info = PluginInfo(metadata=self._meta())
        assert info.name == "test"

    def test_version_delegates_to_metadata(self):
        from navig.core.plugins import PluginInfo

        info = PluginInfo(metadata=self._meta())
        assert info.version == "1.0"

    def test_to_dict_includes_state(self):
        from navig.core.plugins import PluginInfo, PluginState

        info = PluginInfo(metadata=self._meta(), state=PluginState.LOADED)
        d = info.to_dict()
        assert d["state"] == "loaded"
        assert d["name"] == "test"
        assert d["error"] is None

    def test_to_dict_with_error(self):
        from navig.core.plugins import PluginInfo, PluginState

        info = PluginInfo(metadata=self._meta(), state=PluginState.ERROR, error="boom")
        d = info.to_dict()
        assert d["error"] == "boom"
        assert d["state"] == "error"

    def test_to_dict_loaded_at_none(self):
        from navig.core.plugins import PluginInfo

        info = PluginInfo(metadata=self._meta())
        d = info.to_dict()
        assert d["loaded_at"] is None

    def test_to_dict_loaded_at_isoformat(self):
        from navig.core.plugins import PluginInfo, PluginState

        ts = datetime(2024, 6, 1, 12, 0, 0)
        info = PluginInfo(metadata=self._meta(), state=PluginState.LOADED, loaded_at=ts)
        d = info.to_dict()
        assert "2024-06-01" in d["loaded_at"]
