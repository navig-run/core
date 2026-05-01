"""Tests for navig/tui/screens/settings/root.py — SettingsRootScreen."""
from __future__ import annotations

# Textual is stubbed by tests/tui/conftest.py — no try/skip needed.


class TestSettingsRootImport:
    def test_module_importable(self):
        from navig.tui.screens.settings import root
        assert root is not None

    def test_class_exists(self):
        from navig.tui.screens.settings.root import SettingsRootScreen
        assert SettingsRootScreen is not None

    def test_class_name(self):
        from navig.tui.screens.settings.root import SettingsRootScreen
        assert SettingsRootScreen.__name__ == "SettingsRootScreen"

    def test_class_has_compose_or_on_mount(self):
        from navig.tui.screens.settings.root import SettingsRootScreen
        assert hasattr(SettingsRootScreen, "compose") or hasattr(SettingsRootScreen, "on_mount")

    def test_module_file_path_contains_root(self):
        from navig.tui.screens.settings import root
        assert "root" in root.__file__
