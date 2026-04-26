"""Hermetic unit tests for navig.core.capability_registry."""
from __future__ import annotations

import pytest

from navig.core.capability_registry import (
    REGISTRY,
    CapabilityEntry,
    CapabilityTier,
    get_core,
    get_labs,
    get_optional,
    get_tier,
    is_enabled,
)

# ---------------------------------------------------------------------------
# CapabilityTier
# ---------------------------------------------------------------------------


class TestCapabilityTier:
    def test_core_value(self):
        assert CapabilityTier.CORE == "core"

    def test_optional_value(self):
        assert CapabilityTier.OPTIONAL == "optional"

    def test_labs_value(self):
        assert CapabilityTier.LABS == "labs"

    def test_is_str_subclass(self):
        assert isinstance(CapabilityTier.CORE, str)


# ---------------------------------------------------------------------------
# CapabilityEntry
# ---------------------------------------------------------------------------


class TestCapabilityEntry:
    def test_minimal_construction(self):
        entry = CapabilityEntry(tier=CapabilityTier.CORE)
        assert entry.tier == CapabilityTier.CORE
        assert entry.module is None
        assert entry.config_key is None
        assert entry.cli_commands == []
        assert entry.notes == ""

    def test_full_construction(self):
        entry = CapabilityEntry(
            tier=CapabilityTier.OPTIONAL,
            module="my.module",
            config_key="my.enabled",
            optional_dep="mypackage",
            cli_commands=["cmd"],
            notes="Some notes",
        )
        assert entry.module == "my.module"
        assert entry.config_key == "my.enabled"
        assert entry.optional_dep == "mypackage"
        assert entry.cli_commands == ["cmd"]
        assert entry.notes == "Some notes"


# ---------------------------------------------------------------------------
# REGISTRY structure
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_registry_is_dict(self):
        assert isinstance(REGISTRY, dict)

    def test_has_daemon(self):
        assert "daemon" in REGISTRY

    def test_has_telegram(self):
        assert "telegram" in REGISTRY

    def test_has_vault(self):
        assert "vault" in REGISTRY

    def test_daemon_is_core(self):
        assert REGISTRY["daemon"].tier == CapabilityTier.CORE

    def test_mesh_is_optional(self):
        assert REGISTRY["mesh"].tier == CapabilityTier.OPTIONAL

    def test_formations_is_labs(self):
        assert REGISTRY["formations"].tier == CapabilityTier.LABS

    def test_all_entries_are_capability_entry(self):
        for name, entry in REGISTRY.items():
            assert isinstance(entry, CapabilityEntry), f"{name} is not CapabilityEntry"

    def test_all_entries_have_valid_tier(self):
        valid_tiers = set(CapabilityTier)
        for name, entry in REGISTRY.items():
            assert entry.tier in valid_tiers, f"{name} has invalid tier"

    def test_optional_entries_have_config_key(self):
        for name, entry in REGISTRY.items():
            if entry.tier == CapabilityTier.OPTIONAL:
                assert entry.config_key is not None, f"{name} missing config_key"

    def test_core_entries_have_modules(self):
        for name, entry in REGISTRY.items():
            if entry.tier == CapabilityTier.CORE:
                # All core entries should have a module
                assert entry.module is not None, f"{name} missing module"

    def test_infra_commands_cli_includes_host(self):
        assert "host" in REGISTRY["infra_commands"].cli_commands


# ---------------------------------------------------------------------------
# get_tier
# ---------------------------------------------------------------------------


class TestGetTier:
    def test_daemon_is_core(self):
        assert get_tier("daemon") == CapabilityTier.CORE

    def test_mesh_is_optional(self):
        assert get_tier("mesh") == CapabilityTier.OPTIONAL

    def test_formations_is_labs(self):
        assert get_tier("formations") == CapabilityTier.LABS

    def test_unknown_returns_none(self):
        assert get_tier("nonexistent_capability_xyz") is None


# ---------------------------------------------------------------------------
# get_core / get_optional / get_labs
# ---------------------------------------------------------------------------


class TestFilterHelpers:
    def test_get_core_returns_only_core(self):
        core = get_core()
        assert all(v.tier == CapabilityTier.CORE for v in core.values())

    def test_get_core_non_empty(self):
        assert len(get_core()) > 0

    def test_get_optional_returns_only_optional(self):
        opt = get_optional()
        assert all(v.tier == CapabilityTier.OPTIONAL for v in opt.values())

    def test_get_optional_non_empty(self):
        assert len(get_optional()) > 0

    def test_get_labs_returns_only_labs(self):
        labs = get_labs()
        assert all(v.tier == CapabilityTier.LABS for v in labs.values())

    def test_get_labs_non_empty(self):
        assert len(get_labs()) > 0

    def test_tiers_are_exhaustive(self):
        total = len(get_core()) + len(get_optional()) + len(get_labs())
        assert total == len(REGISTRY)

    def test_daemon_in_core(self):
        assert "daemon" in get_core()

    def test_mesh_in_optional(self):
        assert "mesh" in get_optional()

    def test_formations_in_labs(self):
        assert "formations" in get_labs()


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------


class TestIsEnabled:
    def test_core_always_enabled(self):
        assert is_enabled("daemon") is True

    def test_core_enabled_without_config(self):
        assert is_enabled("vault", config=None) is True

    def test_optional_disabled_without_config(self):
        assert is_enabled("mesh") is False

    def test_optional_enabled_with_config_true(self):
        assert is_enabled("mesh", config={"mesh": {"enabled": True}}) is True

    def test_optional_disabled_with_config_false(self):
        assert is_enabled("mesh", config={"mesh": {"enabled": False}}) is False

    def test_unknown_capability_returns_false(self):
        assert is_enabled("unknown_xyz") is False

    def test_nested_config_key_path(self):
        # gateway.deck_enabled
        assert is_enabled("deck", config={"gateway": {"deck_enabled": True}}) is True

    def test_nested_path_missing_key_returns_false(self):
        assert is_enabled("deck", config={"gateway": {}}) is False

    def test_optional_with_empty_config_dict(self):
        assert is_enabled("mesh", config={}) is False

    def test_labs_no_config_key_returns_false(self):
        # genesis_lab has config_key=None — always off
        assert is_enabled("genesis_lab") is False

    def test_labs_with_config_still_false_when_no_key(self):
        assert is_enabled("genesis_lab", config={"anything": True}) is False
