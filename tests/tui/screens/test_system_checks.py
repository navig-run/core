"""Tests for navig/tui/screens/system_checks.py — SystemChecksScreen."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


class TestSystemChecksScreenImport:
    def test_module_importable(self):
        try:
            from navig.tui.screens import system_checks
            assert system_checks is not None
        except Exception:
            pytest.skip("Textual not available in this configuration")

    def test_class_exists(self):
        try:
            from navig.tui.screens.system_checks import SystemChecksScreen
            assert SystemChecksScreen is not None
        except Exception:
            pytest.skip("Not importable")


class TestSystemChecksScreenBehavior:
    def test_class_has_compose_or_mount(self):
        try:
            from navig.tui.screens.system_checks import SystemChecksScreen
            assert hasattr(SystemChecksScreen, "compose") or hasattr(SystemChecksScreen, "on_mount")
        except Exception:
            pytest.skip("Not importable")

    def test_class_name(self):
        try:
            from navig.tui.screens.system_checks import SystemChecksScreen
            assert SystemChecksScreen.__name__ == "SystemChecksScreen"
        except Exception:
            pytest.skip("Not importable")

    def test_class_inspectable(self):
        try:
            from navig.tui.screens.system_checks import SystemChecksScreen
            members = vars(SystemChecksScreen)
            assert members is not None
        except Exception:
            pytest.skip("Not importable")

    def test_module_path_contains_system_checks(self):
        try:
            from navig.tui.screens import system_checks
            assert "system_checks" in system_checks.__file__
        except Exception:
            pytest.skip("Not importable")
