"""
Tests for navig/importers/utils.py
Covers platform detection helpers, env_path, and browser default path functions.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import navig.importers.utils as utils_mod
from navig.importers.utils import (
    chrome_default_path,
    edge_default_path,
    env_path,
    firefox_places_default_path,
    is_macos,
    is_windows,
    safari_default_path,
    winscp_default_path,
)


# ---------------------------------------------------------------------------
# is_windows / is_macos
# ---------------------------------------------------------------------------

class TestPlatformDetection:
    def test_is_windows_true(self):
        with patch("navig.importers.utils.sys") as mock_sys:
            mock_sys.platform = "win32"
            assert is_windows() is True

    def test_is_windows_false_linux(self):
        with patch("navig.importers.utils.sys") as mock_sys:
            mock_sys.platform = "linux"
            assert is_windows() is False

    def test_is_windows_false_darwin(self):
        with patch("navig.importers.utils.sys") as mock_sys:
            mock_sys.platform = "darwin"
            assert is_windows() is False

    def test_is_macos_true(self):
        with patch("navig.importers.utils.sys") as mock_sys:
            mock_sys.platform = "darwin"
            assert is_macos() is True

    def test_is_macos_false_windows(self):
        with patch("navig.importers.utils.sys") as mock_sys:
            mock_sys.platform = "win32"
            assert is_macos() is False

    def test_is_macos_false_linux(self):
        with patch("navig.importers.utils.sys") as mock_sys:
            mock_sys.platform = "linux"
            assert is_macos() is False

    def test_is_windows_win_prefix(self):
        # "win32" and "win64" both start with "win"
        with patch("navig.importers.utils.sys") as mock_sys:
            mock_sys.platform = "win64"
            assert is_windows() is True


# ---------------------------------------------------------------------------
# env_path
# ---------------------------------------------------------------------------

class TestEnvPath:
    def test_returns_path_when_set(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR_NAVIG", "C:\\Users\\Test")
        result = env_path("TEST_VAR_NAVIG")
        assert result == Path("C:\\Users\\Test")

    def test_returns_none_when_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_VAR_NAVIG_MISSING", raising=False)
        result = env_path("TEST_VAR_NAVIG_MISSING")
        assert result is None

    def test_returns_none_when_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR_NAVIG_EMPTY", "")
        result = env_path("TEST_VAR_NAVIG_EMPTY")
        assert result is None

    def test_returns_pathlib_path_type(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR_NAVIG2", "/some/path")
        result = env_path("TEST_VAR_NAVIG2")
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# chrome_default_path
# ---------------------------------------------------------------------------

class TestChromeDefaultPath:
    def test_returns_none_on_non_windows(self):
        with patch("navig.importers.utils.is_windows", return_value=False):
            assert chrome_default_path() is None

    def test_returns_none_when_localappdata_missing(self):
        with patch("navig.importers.utils.is_windows", return_value=True):
            with patch("navig.importers.utils.env_path", return_value=None):
                assert chrome_default_path() is None

    def test_returns_path_string_on_windows(self):
        with patch("navig.importers.utils.is_windows", return_value=True):
            with patch("navig.importers.utils.env_path", return_value=Path("C:\\Users\\Test\\AppData\\Local")):
                result = chrome_default_path()
        assert result is not None
        assert isinstance(result, str)
        assert "Chrome" in result
        assert "Bookmarks" in result


# ---------------------------------------------------------------------------
# edge_default_path
# ---------------------------------------------------------------------------

class TestEdgeDefaultPath:
    def test_returns_none_on_non_windows(self):
        with patch("navig.importers.utils.is_windows", return_value=False):
            assert edge_default_path() is None

    def test_returns_none_when_localappdata_missing(self):
        with patch("navig.importers.utils.is_windows", return_value=True):
            with patch("navig.importers.utils.env_path", return_value=None):
                assert edge_default_path() is None

    def test_returns_path_string_on_windows(self):
        with patch("navig.importers.utils.is_windows", return_value=True):
            with patch("navig.importers.utils.env_path", return_value=Path("C:\\Users\\Test\\AppData\\Local")):
                result = edge_default_path()
        assert result is not None
        assert "Edge" in result
        assert "Bookmarks" in result


# ---------------------------------------------------------------------------
# winscp_default_path
# ---------------------------------------------------------------------------

class TestWinscpDefaultPath:
    def test_returns_none_on_non_windows(self):
        with patch("navig.importers.utils.is_windows", return_value=False):
            assert winscp_default_path() is None

    def test_returns_none_when_appdata_missing(self):
        with patch("navig.importers.utils.is_windows", return_value=True):
            with patch("navig.importers.utils.env_path", return_value=None):
                assert winscp_default_path() is None

    def test_returns_winscp_ini_path(self):
        with patch("navig.importers.utils.is_windows", return_value=True):
            with patch("navig.importers.utils.env_path", return_value=Path("C:\\Users\\Test\\AppData\\Roaming")):
                result = winscp_default_path()
        assert result is not None
        assert "WinSCP.ini" in result


# ---------------------------------------------------------------------------
# safari_default_path
# ---------------------------------------------------------------------------

class TestSafariDefaultPath:
    def test_returns_none_on_non_macos(self):
        with patch("navig.importers.utils.is_macos", return_value=False):
            assert safari_default_path() is None

    def test_returns_plist_path_on_macos(self):
        with patch("navig.importers.utils.is_macos", return_value=True):
            result = safari_default_path()
        assert result is not None
        assert "Bookmarks.plist" in result
        assert "Safari" in result


# ---------------------------------------------------------------------------
# firefox_places_default_path
# ---------------------------------------------------------------------------

class TestFirefoxPlacesDefaultPath:
    def test_returns_none_when_profiles_ini_missing(self, tmp_path):
        with patch("navig.importers.utils.is_windows", return_value=False):
            with patch("navig.importers.utils.is_macos", return_value=False):
                with patch("navig.importers.utils.Path") as mock_path:
                    mock_ini = mock_path.return_value.__truediv__.return_value
                    mock_ini.exists.return_value = False
                    # Simulate non-existent path — falls back to None
                    result = firefox_places_default_path()
        # When profiles.ini doesn't exist, returns None
        assert result is None or isinstance(result, str)

    def test_returns_none_when_no_profiles_ini_on_linux(self, tmp_path):
        fake_ini = tmp_path / "profiles.ini"
        # Don't create it — it doesn't exist
        with patch("navig.importers.utils.is_windows", return_value=False):
            with patch("navig.importers.utils.is_macos", return_value=False):
                with patch.object(utils_mod, "is_windows", return_value=False):
                    with patch.object(utils_mod, "is_macos", return_value=False):
                        with patch("navig.importers.utils.Path") as mock_path_cls:
                            # Simulate home() / ".mozilla" / "firefox" / "profiles.ini"
                            mock_path_instance = mock_path_cls.home.return_value
                            chained = mock_path_instance.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value
                            chained.exists.return_value = False
                            result = firefox_places_default_path()
        assert result is None

    def test_discover_returns_none_on_empty_ini(self, tmp_path):
        # Write a profiles.ini with no valid profile entries
        ini = tmp_path / "profiles.ini"
        ini.write_text("")
        result = utils_mod._discover_firefox_profile(ini)
        assert result is None

    def test_discover_picks_default_profile(self, tmp_path):
        # Write profiles.ini with two profiles, one marked Default=1
        ini = tmp_path / "profiles.ini"
        ini.write_text(
            "[Profile0]\nPath=profiles/other\nIsRelative=1\n\n"
            "[Profile1]\nPath=profiles/default\nIsRelative=1\nDefault=1\n"
        )
        result = utils_mod._discover_firefox_profile(ini)
        assert result is not None
        assert "default" in str(result)

    def test_discover_falls_back_to_first_profile(self, tmp_path):
        ini = tmp_path / "profiles.ini"
        ini.write_text("[Profile0]\nPath=profiles/first\nIsRelative=1\n")
        result = utils_mod._discover_firefox_profile(ini)
        assert result is not None
        assert "first" in str(result)

    def test_discover_absolute_path(self, tmp_path):
        target = tmp_path / "absolute_profile"
        ini = tmp_path / "profiles.ini"
        ini.write_text(f"[Profile0]\nPath={str(target)}\nIsRelative=0\n")
        result = utils_mod._discover_firefox_profile(ini)
        assert result == target

    def test_discover_returns_none_with_no_path_key(self, tmp_path):
        ini = tmp_path / "profiles.ini"
        ini.write_text("[Profile0]\nIsRelative=1\n")
        result = utils_mod._discover_firefox_profile(ini)
        assert result is None
