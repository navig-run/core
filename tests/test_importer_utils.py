"""Tests for navig.importers.utils — platform detection and browser path helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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


# ──────────────────────────────────────────────────────────────
# is_windows / is_macos
# ──────────────────────────────────────────────────────────────


class TestPlatformDetection:
    def test_is_windows_true_on_win32(self):
        with patch.object(sys, "platform", "win32"):
            assert is_windows() is True

    def test_is_windows_false_on_linux(self):
        with patch.object(sys, "platform", "linux"):
            assert is_windows() is False

    def test_is_windows_false_on_darwin(self):
        with patch.object(sys, "platform", "darwin"):
            assert is_windows() is False

    def test_is_macos_true_on_darwin(self):
        with patch.object(sys, "platform", "darwin"):
            assert is_macos() is True

    def test_is_macos_false_on_linux(self):
        with patch.object(sys, "platform", "linux"):
            assert is_macos() is False

    def test_is_macos_false_on_win32(self):
        with patch.object(sys, "platform", "win32"):
            assert is_macos() is False

    def test_at_most_one_is_true_on_darwin(self):
        with patch.object(sys, "platform", "darwin"):
            assert not (is_windows() and is_macos())

    def test_at_most_one_is_true_on_win32(self):
        with patch.object(sys, "platform", "win32"):
            assert not (is_windows() and is_macos())


# ──────────────────────────────────────────────────────────────
# env_path
# ──────────────────────────────────────────────────────────────


class TestEnvPath:
    def test_returns_path_when_set(self, monkeypatch):
        monkeypatch.setenv("NAVIG_TEST_PATH_XYZ", "C:\\Users\\test")
        result = env_path("NAVIG_TEST_PATH_XYZ")
        assert isinstance(result, Path)
        assert str(result) == "C:\\Users\\test"

    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("NAVIG_TEST_PATH_UNSET", raising=False)
        assert env_path("NAVIG_TEST_PATH_UNSET") is None

    def test_returns_none_for_empty_value(self, monkeypatch):
        monkeypatch.setenv("NAVIG_TEST_EMPTY", "")
        assert env_path("NAVIG_TEST_EMPTY") is None


# ──────────────────────────────────────────────────────────────
# Browser path helpers (platform-conditional)
# ──────────────────────────────────────────────────────────────


class TestBrowserPaths:
    def test_chrome_path_none_on_non_windows(self):
        with patch.object(sys, "platform", "linux"):
            assert chrome_default_path() is None

    def test_edge_path_none_on_non_windows(self):
        with patch.object(sys, "platform", "linux"):
            assert edge_default_path() is None

    def test_winscp_path_none_on_non_windows(self):
        with patch.object(sys, "platform", "linux"):
            assert winscp_default_path() is None

    def test_safari_path_none_on_non_macos(self):
        with patch.object(sys, "platform", "win32"):
            assert safari_default_path() is None

    def test_safari_path_none_on_linux(self):
        with patch.object(sys, "platform", "linux"):
            assert safari_default_path() is None

    def test_chrome_path_returns_string_on_windows_with_localappdata(self, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")
        with patch.object(sys, "platform", "win32"):
            result = chrome_default_path()
        assert result is not None
        assert "Chrome" in result
        assert "Bookmarks" in result

    def test_edge_path_returns_string_on_windows_with_localappdata(self, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")
        with patch.object(sys, "platform", "win32"):
            result = edge_default_path()
        assert result is not None
        assert "Edge" in result

    def test_winscp_path_returns_string_on_windows_with_appdata(self, monkeypatch):
        monkeypatch.setenv("APPDATA", "C:\\Users\\test\\AppData\\Roaming")
        with patch.object(sys, "platform", "win32"):
            result = winscp_default_path()
        assert result is not None
        assert "WinSCP" in result

    def test_safari_path_on_macos(self):
        with patch.object(sys, "platform", "darwin"):
            result = safari_default_path()
        assert result is not None
        assert "Safari" in result
        assert "Bookmarks.plist" in result

    def test_chrome_path_none_when_no_localappdata(self, monkeypatch):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        with patch.object(sys, "platform", "win32"):
            assert chrome_default_path() is None

    def test_firefox_path_none_on_linux_without_profile(self, tmp_path):
        with patch.object(sys, "platform", "linux"), \
             patch("pathlib.Path.home", return_value=tmp_path):
            result = firefox_places_default_path()
        # Profile doesn't exist → should be None
        assert result is None
