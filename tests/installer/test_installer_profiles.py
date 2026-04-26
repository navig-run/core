"""Tests for navig.installer.profiles — PROFILE_MODULES, DEFAULT_PROFILE, VALID_PROFILES."""
from __future__ import annotations

import pytest

from navig.installer.profiles import DEFAULT_PROFILE, PROFILE_MODULES, VALID_PROFILES


class TestProfileModules:
    def test_all_expected_profiles_present(self):
        for name in ("node", "operator", "architect", "system_standard", "system_deep"):
            assert name in PROFILE_MODULES

    def test_each_profile_is_list_of_strings(self):
        for name, modules in PROFILE_MODULES.items():
            assert isinstance(modules, list), f"{name} should be a list"
            for m in modules:
                assert isinstance(m, str)

    def test_node_minimal_modules(self):
        assert "config_paths" in PROFILE_MODULES["node"]
        assert "core_cli" in PROFILE_MODULES["node"]

    def test_operator_extends_node(self):
        node_set = set(PROFILE_MODULES["node"])
        op_set = set(PROFILE_MODULES["operator"])
        # operator must include at least all node modules
        assert node_set.issubset(op_set)

    def test_architect_extends_operator(self):
        op_set = set(PROFILE_MODULES["operator"])
        arch_set = set(PROFILE_MODULES["architect"])
        assert op_set.issubset(arch_set)

    def test_system_deep_includes_tray_and_persona_assets(self):
        modules = PROFILE_MODULES["system_deep"]
        assert "tray" in modules
        assert "persona_assets" in modules

    def test_no_duplicate_modules_per_profile(self):
        for name, modules in PROFILE_MODULES.items():
            assert len(modules) == len(set(modules)), f"{name} has duplicates"


class TestDefaultProfile:
    def test_default_is_operator(self):
        assert DEFAULT_PROFILE == "operator"

    def test_default_exists_in_profile_modules(self):
        assert DEFAULT_PROFILE in PROFILE_MODULES


class TestValidProfiles:
    def test_valid_profiles_matches_profile_modules_keys(self):
        assert set(VALID_PROFILES) == set(PROFILE_MODULES.keys())

    def test_valid_profiles_is_list(self):
        assert isinstance(VALID_PROFILES, list)
