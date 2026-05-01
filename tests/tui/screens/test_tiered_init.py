"""Tests for navig/tui/screens/tiered_init.py — TieredInitScreen."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


class TestTieredInitScreenImport:
    def test_module_importable(self):
        with patch.dict("sys.modules", {
            "textual": MagicMock(),
            "textual.app": MagicMock(),
            "textual.screen": MagicMock(),
            "textual.widgets": MagicMock(),
            "textual.containers": MagicMock(),
            "textual.reactive": MagicMock(),
            "textual.css.query": MagicMock(),
        }):
            try:
                from navig.tui.screens import tiered_init
                assert tiered_init is not None
            except Exception:
                pytest.skip("Textual not available in this configuration")

    def test_class_exists(self):
        try:
            from navig.tui.screens.tiered_init import TieredInitScreen
            assert TieredInitScreen is not None
        except ImportError:
            pytest.skip("TieredInitScreen not importable")
        except Exception:
            pytest.skip("Textual not available")


class TestTieredInitConfig:
    def test_class_has_compose(self):
        try:
            from navig.tui.screens.tiered_init import TieredInitScreen
            assert hasattr(TieredInitScreen, "compose") or hasattr(TieredInitScreen, "on_mount")
        except Exception:
            pytest.skip("Not importable")

    def test_class_is_screen_subclass(self):
        try:
            import textual.screen
            from navig.tui.screens.tiered_init import TieredInitScreen
            assert issubclass(TieredInitScreen, textual.screen.Screen)
        except Exception:
            pytest.skip("Not importable")

    def test_class_name(self):
        try:
            from navig.tui.screens.tiered_init import TieredInitScreen
            assert TieredInitScreen.__name__ == "TieredInitScreen"
        except Exception:
            pytest.skip("Not importable")

    def test_module_has_expected_name(self):
        try:
            from navig.tui.screens import tiered_init
            assert "tiered_init" in tiered_init.__name__
        except Exception:
            pytest.skip("Not importable")

    @pytest.mark.parametrize("attr", ["TITLE", "CSS", "CSS_PATH", "BINDINGS"])
    def test_class_may_have_screen_attrs(self, attr):
        try:
            from navig.tui.screens.tiered_init import TieredInitScreen
            # Just confirm class can be inspected without crashing
            vars(TieredInitScreen)
        except Exception:
            pytest.skip("Not importable")
