"""Unit tests for navig.platform.paths — cross-platform path resolution.

Strategy:
  - Env-override tests: set NAVIG_*_DIR env vars; verify the override is honored.
  - Derived-path tests: verify sub-paths are expressed relative to parent functions.
  - Return-type tests: every public path function returns a Path.
  - OS-detection tests: monkeypatch sys.platform + reset the module cache.

All tests are hermetic: no filesystem mutation, no network, no subprocess.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import navig.platform.paths as paths_mod
from navig.platform.paths import (
    blackbox_dir,
    cache_dir,
    config_dir,
    current_os,
    data_dir,
    debug_log_path,
    is_linux,
    is_macos,
    is_unix,
    is_windows,
    is_wsl,
    log_dir,
    workspace_dir,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_os_cache() -> None:
    """Reset the module-level OS detection cache so tests stay independent."""
    paths_mod._DETECTED_OS = None


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------


class TestCurrentOs:
    def teardown_method(self) -> None:  # noqa: ANN201
        _reset_os_cache()

    def test_current_os_returns_string(self) -> None:
        assert isinstance(current_os(), str)

    def test_current_os_is_known_value(self) -> None:
        assert current_os() in {"windows", "linux", "macos", "wsl"}

    def test_current_os_cached(self) -> None:
        a = current_os()
        b = current_os()
        assert a == b
        assert paths_mod._DETECTED_OS is not None

    def test_windows_detection(self) -> None:
        _reset_os_cache()
        with patch.object(sys, "platform", "win32"):
            result = current_os()
        assert result == "windows"
        _reset_os_cache()

    def test_macos_detection(self) -> None:
        _reset_os_cache()
        with patch.object(sys, "platform", "darwin"):
            result = current_os()
        assert result == "macos"
        _reset_os_cache()

    def test_unknown_platform_falls_back_to_linux(self) -> None:
        _reset_os_cache()
        with patch.object(sys, "platform", "freebsd13"):
            result = current_os()
        assert result == "linux"
        _reset_os_cache()


class TestOsPredicates:
    def teardown_method(self) -> None:  # noqa: ANN201
        _reset_os_cache()

    def test_exactly_one_os_predicate_true(self) -> None:
        predicates = [is_windows(), is_linux(), is_macos(), is_wsl()]
        # Exactly one must be True
        assert predicates.count(True) == 1

    def test_is_unix_false_on_windows(self) -> None:
        _reset_os_cache()
        with patch.object(sys, "platform", "win32"):
            paths_mod._DETECTED_OS = None
            result = is_unix()
        _reset_os_cache()
        assert result is False

    def test_is_unix_true_on_linux(self) -> None:
        paths_mod._DETECTED_OS = "linux"
        result = is_unix()
        _reset_os_cache()
        assert result is True

    def test_is_unix_true_on_macos(self) -> None:
        paths_mod._DETECTED_OS = "macos"
        result = is_unix()
        _reset_os_cache()
        assert result is True


# ---------------------------------------------------------------------------
# config_dir — env override
# ---------------------------------------------------------------------------


class TestConfigDir:
    def test_returns_path(self) -> None:
        assert isinstance(config_dir(), Path)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        assert config_dir() == tmp_path

    def test_env_override_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
        result = config_dir()
        assert isinstance(result, Path)
        assert "navig" in result.name.lower()

    def test_non_empty_path(self) -> None:
        assert len(str(config_dir())) > 0


# ---------------------------------------------------------------------------
# data_dir — env override + derived path
# ---------------------------------------------------------------------------


class TestDataDir:
    def test_returns_path(self) -> None:
        assert isinstance(data_dir(), Path)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
        assert data_dir() == tmp_path

    def test_default_is_under_config_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_DATA_DIR", raising=False)
        monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        result = data_dir()
        cfg = config_dir()
        assert str(result).startswith(str(cfg))


# ---------------------------------------------------------------------------
# log_dir — env override
# ---------------------------------------------------------------------------


class TestLogDir:
    def test_returns_path(self) -> None:
        assert isinstance(log_dir(), Path)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("NAVIG_LOG_DIR", str(tmp_path))
        assert log_dir() == tmp_path

    def test_env_override_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        result = log_dir()
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# Derived path helpers
# ---------------------------------------------------------------------------


class TestDerivedPaths:
    def test_workspace_dir_under_config_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
        assert workspace_dir() == config_dir() / "workspace"

    def test_blackbox_dir_under_data_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_DATA_DIR", raising=False)
        assert blackbox_dir() == data_dir() / "blackbox"

    def test_debug_log_path_under_log_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        assert debug_log_path() == log_dir() / "debug.log"

    def test_debug_log_path_is_log_file(self) -> None:
        assert debug_log_path().name == "debug.log"

    def test_workspace_dir_returns_path(self) -> None:
        assert isinstance(workspace_dir(), Path)

    def test_blackbox_dir_returns_path(self) -> None:
        assert isinstance(blackbox_dir(), Path)

    def test_debug_log_path_returns_path(self) -> None:
        assert isinstance(debug_log_path(), Path)


# ---------------------------------------------------------------------------
# cache_dir — env override
# ---------------------------------------------------------------------------


class TestCacheDir:
    def test_returns_path(self) -> None:
        assert isinstance(cache_dir(), Path)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("NAVIG_CACHE_DIR", str(tmp_path))
        assert cache_dir() == tmp_path

    def test_env_override_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_CACHE_DIR", raising=False)
        result = cache_dir()
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# OS-specific log / cache paths (set _DETECTED_OS directly — no cache to reset)
# ---------------------------------------------------------------------------


class TestOsSpecificPaths:
    def setup_method(self) -> None:
        _reset_os_cache()

    def teardown_method(self) -> None:
        _reset_os_cache()

    def test_windows_log_dir_uses_localappdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "windows"
        result = log_dir()
        assert "navig" in str(result).lower()
        assert "logs" in str(result).lower()

    def test_macos_log_dir_uses_library_logs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "macos"
        result = log_dir()
        assert "Library" in str(result)
        assert "Logs" in str(result)

    def test_linux_log_dir_fallback_uses_local_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "linux"
        result = log_dir()
        assert ".local" in str(result)
        assert "navig" in str(result).lower()

    def test_linux_log_dir_xdg_state_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "linux"
        result = log_dir()
        assert str(result).startswith(str(tmp_path))

    def test_windows_cache_dir_uses_localappdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_CACHE_DIR", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "windows"
        result = cache_dir()
        assert "navig" in str(result).lower()
        assert "cache" in str(result).lower()

    def test_macos_cache_dir_uses_library_caches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_CACHE_DIR", raising=False)
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "macos"
        result = cache_dir()
        assert "Library" in str(result)
        assert "Caches" in str(result)
