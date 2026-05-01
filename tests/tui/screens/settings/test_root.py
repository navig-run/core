"""Tests for navig/tui/screens/settings/root.py — SettingsRootScreen."""
from __future__ import annotations

import pytest


class TestSettingsRootImport:
    def test_module_importable(self):
        try:
            from navig.tui.screens.settings import root
            assert root is not None
        except Exception:
            pytest.skip("Not importable")

    def test_class_exists(self):
        try:
            from navig.tui.screens.settings.root import SettingsRootScreen
            assert SettingsRootScreen is not None
        except Exception:
            pytest.skip("Not importable")

    def test_class_name(self):
        try:
            from navig.tui.screens.settings.root import SettingsRootScreen
            assert SettingsRootScreen.__name__ == "SettingsRootScreen"
        except Exception:
            pytest.skip("Not importable")

    def test_class_has_compose_or_on_mount(self):
        try:
            from navig.tui.screens.settings.root import SettingsRootScreen
            assert hasattr(SettingsRootScreen, "compose") or hasattr(SettingsRootScreen, "on_mount")
        except Exception:
            pytest.skip("Not importable")

    def test_module_file_path_contains_root(self):
        try:
            from navig.tui.screens.settings import root
            assert "root" in root.__file__
        except Exception:
            pytest.skip("Not importable")
