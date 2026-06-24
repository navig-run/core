"""Tests for navig/tui/screens/tiered_init.py — TieredInitScreen."""
from __future__ import annotations

import pytest

# Textual is stubbed by tests/tui/conftest.py — no try/skip needed.


class TestTieredInitScreenImport:
    def test_module_importable(self):
        from navig.tui.screens import tiered_init
        assert tiered_init is not None

    def test_class_exists(self):
        from navig.tui.screens.tiered_init import TieredInitScreen
        assert TieredInitScreen is not None


class TestTieredInitConfig:
    def test_class_has_compose(self):
        from navig.tui.screens.tiered_init import TieredInitScreen
        assert hasattr(TieredInitScreen, "compose") or hasattr(TieredInitScreen, "on_mount")

    def test_class_is_screen_subclass(self):
        from textual.screen import Screen
        from navig.tui.screens.tiered_init import TieredInitScreen
        assert issubclass(TieredInitScreen, Screen)

    def test_class_name(self):
        from navig.tui.screens.tiered_init import TieredInitScreen
        assert TieredInitScreen.__name__ == "TieredInitScreen"

    def test_module_has_expected_name(self):
        from navig.tui.screens import tiered_init
        assert "tiered_init" in tiered_init.__name__

    @pytest.mark.parametrize("attr", ["TITLE", "CSS", "CSS_PATH", "BINDINGS"])
    def test_class_may_have_screen_attrs(self, attr):
        from navig.tui.screens.tiered_init import TieredInitScreen
        # Confirm class can be inspected without crashing
        vars(TieredInitScreen)
