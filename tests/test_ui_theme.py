"""Tests for navig.ui.theme — safe mode, nerd font detection, style constants."""
from __future__ import annotations

import importlib
import sys


class TestDetectSafeMode:
    def test_safe_mode_enabled_by_env(self, monkeypatch):
        monkeypatch.setenv("NAVIG_SAFE_MODE", "1")
        from navig.ui import theme as theme_mod
        assert theme_mod._detect_safe_mode() is True

    def test_safe_mode_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("NAVIG_SAFE_MODE", "0")
        from navig.ui import theme as theme_mod
        assert theme_mod._detect_safe_mode() is False

    def test_safe_mode_disabled_for_utf8(self, monkeypatch):
        monkeypatch.delenv("NAVIG_SAFE_MODE", raising=False)
        from unittest.mock import MagicMock
        fake_stdout = MagicMock()
        fake_stdout.encoding = "utf-8"
        monkeypatch.setattr("sys.stdout", fake_stdout)
        from navig.ui import theme as theme_mod
        assert theme_mod._detect_safe_mode() is False


class TestDetectNerdFont:
    def test_forced_on_by_env(self, monkeypatch):
        monkeypatch.setenv("NAVIG_NERD_FONT", "1")
        from navig.ui import theme as theme_mod
        assert theme_mod._detect_nerd_font() is True

    def test_forced_off_by_env(self, monkeypatch):
        monkeypatch.setenv("NAVIG_NERD_FONT", "0")
        from navig.ui import theme as theme_mod
        assert theme_mod._detect_nerd_font() is False

    def test_suppressed_in_ci(self, monkeypatch):
        monkeypatch.delenv("NAVIG_NERD_FONT", raising=False)
        monkeypatch.setenv("CI", "1")
        from navig.ui import theme as theme_mod
        assert theme_mod._detect_nerd_font() is False

    def test_suppressed_when_no_color(self, monkeypatch):
        monkeypatch.delenv("NAVIG_NERD_FONT", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("NO_COLOR", "1")
        from navig.ui import theme as theme_mod
        assert theme_mod._detect_nerd_font() is False


class TestStyleConstants:
    def test_severity_style_has_all_keys(self):
        from navig.ui.theme import SEVERITY_STYLE
        for key in ("ok", "info", "warn", "critical"):
            assert key in SEVERITY_STYLE

    def test_color_style_has_standard_colors(self):
        from navig.ui.theme import COLOR_STYLE
        for color in ("cyan", "green", "yellow", "red"):
            assert color in COLOR_STYLE

    def test_style_constants_are_strings(self):
        from navig.ui import theme
        for attr in ("STYLE_STATUS_OK", "STYLE_STATUS_WARN", "STYLE_STATUS_FAIL",
                     "STYLE_COMMAND", "STYLE_DIM", "STYLE_AI"):
            assert isinstance(getattr(theme, attr), str)

    def test_render_mode_is_valid(self):
        from navig.ui.theme import RENDER_MODE
        assert RENDER_MODE in ("safe", "rich")

    def test_console_is_rich_console(self):
        from rich.console import Console
        from navig.ui.theme import console
        assert isinstance(console, Console)
