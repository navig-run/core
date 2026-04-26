"""Tests for navig.importers.utils — platform helpers and default path functions."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.importers import utils as ut


class TestPlatformDetect:
    def test_is_windows_true_on_win32(self) -> None:
        with patch.object(sys, "platform", "win32"):
            assert ut.is_windows() is True

    def test_is_windows_false_on_darwin(self) -> None:
        with patch.object(sys, "platform", "darwin"):
            assert ut.is_windows() is False

    def test_is_macos_true_on_darwin(self) -> None:
        with patch.object(sys, "platform", "darwin"):
            assert ut.is_macos() is True

    def test_is_macos_false_on_linux(self) -> None:
        with patch.object(sys, "platform", "linux"):
            assert ut.is_macos() is False


class TestEnvPath:
    def test_returns_path_when_var_set(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {"TEST_ENV_PATH": str(tmp_path)}):
            result = ut.env_path("TEST_ENV_PATH")
        assert result == tmp_path

    def test_returns_none_when_var_missing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = ut.env_path("DEFINITELY_ABSENT_XYZ")
        assert result is None

    def test_returns_none_when_var_empty(self) -> None:
        with patch.dict("os.environ", {"EMPTY_VAR": ""}):
            result = ut.env_path("EMPTY_VAR")
        assert result is None


class TestChromeDefaultPath:
    def test_returns_none_on_non_windows(self) -> None:
        with patch.object(sys, "platform", "linux"):
            assert ut.chrome_default_path() is None

    def test_returns_path_on_windows(self, tmp_path: Path) -> None:
        with patch.object(sys, "platform", "win32"), \
             patch.dict("os.environ", {"LOCALAPPDATA": str(tmp_path)}):
            result = ut.chrome_default_path()
        assert result is not None
        assert "Chrome" in result
        assert "Bookmarks" in result


class TestEdgeDefaultPath:
    def test_returns_none_on_non_windows(self) -> None:
        with patch.object(sys, "platform", "darwin"):
            assert ut.edge_default_path() is None

    def test_returns_path_on_windows(self, tmp_path: Path) -> None:
        with patch.object(sys, "platform", "win32"), \
             patch.dict("os.environ", {"LOCALAPPDATA": str(tmp_path)}):
            result = ut.edge_default_path()
        assert result is not None
        assert "Edge" in result


class TestWinscpDefaultPath:
    def test_returns_none_on_non_windows(self) -> None:
        with patch.object(sys, "platform", "linux"):
            assert ut.winscp_default_path() is None

    def test_returns_ini_path_on_windows(self, tmp_path: Path) -> None:
        with patch.object(sys, "platform", "win32"), \
             patch.dict("os.environ", {"APPDATA": str(tmp_path)}):
            result = ut.winscp_default_path()
        assert result is not None
        assert "WinSCP.ini" in result


class TestSafariDefaultPath:
    def test_returns_none_on_non_macos(self) -> None:
        with patch.object(sys, "platform", "win32"):
            assert ut.safari_default_path() is None

    def test_returns_plist_path_on_macos(self) -> None:
        with patch.object(sys, "platform", "darwin"):
            result = ut.safari_default_path()
        assert result is not None
        assert "Bookmarks.plist" in result
        assert "Safari" in result
