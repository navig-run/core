"""Tests for navig/tui/screens/welcome.py — WelcomeScreen."""
from __future__ import annotations

# Textual is stubbed by tests/tui/conftest.py — no try/skip needed.


class TestWelcomeScreenImport:
    def test_module_importable(self):
        from navig.tui.screens import welcome
        assert welcome is not None

    def test_class_exists(self):
        from navig.tui.screens.welcome import WelcomeScreen
        assert WelcomeScreen is not None

    def test_class_name(self):
        from navig.tui.screens.welcome import WelcomeScreen
        assert WelcomeScreen.__name__ == "WelcomeScreen"

    def test_class_has_compose_or_on_mount(self):
        from navig.tui.screens.welcome import WelcomeScreen
        assert hasattr(WelcomeScreen, "compose") or hasattr(WelcomeScreen, "on_mount")

    def test_class_is_type(self):
        from navig.tui.screens.welcome import WelcomeScreen
        assert isinstance(WelcomeScreen, type)
