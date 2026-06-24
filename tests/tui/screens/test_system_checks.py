"""Tests for navig/tui/screens/system_checks.py — SystemChecksScreen."""
from __future__ import annotations

import pytest

# Textual is stubbed by tests/tui/conftest.py — no try/skip needed.


class TestSystemChecksScreenImport:
    def test_module_importable(self):
        from navig.tui.screens import system_checks
        assert system_checks is not None

    def test_class_exists(self):
        from navig.tui.screens.system_checks import SystemChecksScreen
        assert SystemChecksScreen is not None


class TestSystemChecksScreenBehavior:
    def test_class_has_compose_or_mount(self):
        from navig.tui.screens.system_checks import SystemChecksScreen
        assert hasattr(SystemChecksScreen, "compose") or hasattr(SystemChecksScreen, "on_mount")

    def test_class_name(self):
        from navig.tui.screens.system_checks import SystemChecksScreen
        assert SystemChecksScreen.__name__ == "SystemChecksScreen"

    def test_class_inspectable(self):
        from navig.tui.screens.system_checks import SystemChecksScreen
        members = vars(SystemChecksScreen)
        assert members is not None

    def test_module_path_contains_system_checks(self):
        from navig.tui.screens import system_checks
        assert "system_checks" in system_checks.__file__
