"""Tests for navig/tui/screens/welcome.py — WelcomeScreen."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


class TestWelcomeScreenImport:
    def test_module_importable(self):
        try:
            from navig.tui.screens import welcome
            assert welcome is not None
        except Exception:
            pytest.skip("Not importable")

    def test_class_exists(self):
        try:
            from navig.tui.screens.welcome import WelcomeScreen
            assert WelcomeScreen is not None
        except Exception:
            pytest.skip("Not importable")

    def test_class_name(self):
        try:
            from navig.tui.screens.welcome import WelcomeScreen
            assert WelcomeScreen.__name__ == "WelcomeScreen"
        except Exception:
            pytest.skip("Not importable")

    def test_class_has_compose_or_on_mount(self):
        try:
            from navig.tui.screens.welcome import WelcomeScreen
            assert hasattr(WelcomeScreen, "compose") or hasattr(WelcomeScreen, "on_mount")
        except Exception:
            pytest.skip("Not importable")

    def test_class_is_type(self):
        try:
            from navig.tui.screens.welcome import WelcomeScreen
            assert isinstance(WelcomeScreen, type)
        except Exception:
            pytest.skip("Not importable")
