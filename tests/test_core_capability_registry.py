"""
Batch 79: hermetic unit tests for navig/core/capability_registry.py
  - CapabilityTier enum
  - CapabilityEntry dataclass
  - get_tier(), get_core(), get_optional(), get_labs(), is_enabled()
  - REGISTRY consistency invariants
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# CapabilityTier enum
# ---------------------------------------------------------------------------

class TestCapabilityTier:
    def test_values(self) -> None:
        from navig.core.capability_registry import CapabilityTier
        assert CapabilityTier.CORE == "core"
        assert CapabilityTier.OPTIONAL == "optional"
        assert CapabilityTier.LABS == "labs"

    def test_three_tiers(self) -> None:
        from navig.core.capability_registry import CapabilityTier
        assert len(list(CapabilityTier)) == 3


# ---------------------------------------------------------------------------
# CapabilityEntry dataclass
# ---------------------------------------------------------------------------

class TestCapabilityEntry:
    def test_defaults(self) -> None:
        from navig.core.capability_registry import CapabilityEntry, CapabilityTier
        entry = CapabilityEntry(tier=CapabilityTier.CORE)
        assert entry.module is None
        assert entry.config_key is None
        assert entry.optional_dep is None
        assert entry.cli_commands == []
        assert entry.notes == ""

    def test_with_all_fields(self) -> None:
        from navig.core.capability_registry import CapabilityEntry, CapabilityTier
        entry = CapabilityEntry(
            tier=CapabilityTier.OPTIONAL,
            module="navig.brain",
            config_key="brain.enabled",
            optional_dep="brain",
            cli_commands=["brain"],
            notes="Memory plugin",
        )
        assert entry.tier == CapabilityTier.OPTIONAL
        assert entry.module == "navig.brain"
        assert entry.config_key == "brain.enabled"


# ---------------------------------------------------------------------------
# REGISTRY invariants
# ---------------------------------------------------------------------------

class TestRegistryInvariants:
    def test_registry_not_empty(self) -> None:
        from navig.core.capability_registry import REGISTRY
        assert len(REGISTRY) > 0

    def test_all_entries_have_tier(self) -> None:
        from navig.core.capability_registry import REGISTRY, CapabilityTier
        for name, entry in REGISTRY.items():
            assert isinstance(entry.tier, CapabilityTier), f"{name} has invalid tier"

    def test_known_core_capabilities_present(self) -> None:
        from navig.core.capability_registry import REGISTRY, CapabilityTier
        core_keys = [k for k, v in REGISTRY.items() if v.tier == CapabilityTier.CORE]
        # Should have common ones
        assert "vault" in core_keys or "agent" in core_keys

    def test_cli_commands_is_list(self) -> None:
        from navig.core.capability_registry import REGISTRY
        for name, entry in REGISTRY.items():
            assert isinstance(entry.cli_commands, list), f"{name}.cli_commands is not a list"


# ---------------------------------------------------------------------------
# get_tier()
# ---------------------------------------------------------------------------

class TestGetTier:
    def test_known_core_capability(self) -> None:
        from navig.core.capability_registry import get_tier, CapabilityTier
        # 'vault' or 'agent' should be CORE
        result = get_tier("vault") or get_tier("agent")
        assert result == CapabilityTier.CORE

    def test_unknown_capability_returns_none(self) -> None:
        from navig.core.capability_registry import get_tier
        assert get_tier("nonexistent_capability_xyz") is None

    def test_returns_correct_tier_for_each_type(self) -> None:
        from navig.core.capability_registry import REGISTRY, get_tier
        for name, entry in REGISTRY.items():
            assert get_tier(name) == entry.tier


# ---------------------------------------------------------------------------
# get_core(), get_optional(), get_labs()
# ---------------------------------------------------------------------------

class TestGetTierFilters:
    def test_get_core_all_have_core_tier(self) -> None:
        from navig.core.capability_registry import get_core, CapabilityTier
        for name, entry in get_core().items():
            assert entry.tier == CapabilityTier.CORE

    def test_get_optional_all_have_optional_tier(self) -> None:
        from navig.core.capability_registry import get_optional, CapabilityTier
        for name, entry in get_optional().items():
            assert entry.tier == CapabilityTier.OPTIONAL

    def test_get_labs_all_have_labs_tier(self) -> None:
        from navig.core.capability_registry import get_labs, CapabilityTier
        for name, entry in get_labs().items():
            assert entry.tier == CapabilityTier.LABS

    def test_tiers_are_disjoint(self) -> None:
        from navig.core.capability_registry import get_core, get_optional, get_labs
        core_keys = set(get_core())
        opt_keys = set(get_optional())
        labs_keys = set(get_labs())
        assert core_keys.isdisjoint(opt_keys)
        assert core_keys.isdisjoint(labs_keys)
        assert opt_keys.isdisjoint(labs_keys)

    def test_all_tiers_cover_full_registry(self) -> None:
        from navig.core.capability_registry import REGISTRY, get_core, get_optional, get_labs
        all_from_helpers = set(get_core()) | set(get_optional()) | set(get_labs())
        assert all_from_helpers == set(REGISTRY)


# ---------------------------------------------------------------------------
# is_enabled()
# ---------------------------------------------------------------------------

class TestIsEnabled:
    def test_core_always_enabled_no_config(self) -> None:
        from navig.core.capability_registry import is_enabled, get_core
        for name in get_core():
            assert is_enabled(name) is True, f"CORE capability {name!r} should always be enabled"

    def test_unknown_capability_returns_false(self) -> None:
        from navig.core.capability_registry import is_enabled
        assert is_enabled("totally_unknown_xyz") is False

    def test_optional_without_config_returns_false(self) -> None:
        from navig.core.capability_registry import is_enabled, get_optional
        for name in list(get_optional())[:3]:  # test a few
            assert is_enabled(name, config=None) is False

    def test_optional_with_matching_config_returns_true(self) -> None:
        from navig.core.capability_registry import get_optional, is_enabled
        for name, entry in get_optional().items():
            if entry.config_key:
                # Build a config dict that enables this capability
                parts = entry.config_key.split(".")
                config: dict = {}
                current = config
                for part in parts[:-1]:
                    current[part] = {}
                    current = current[part]
                current[parts[-1]] = True
                assert is_enabled(name, config=config) is True
                break  # test only one

    def test_optional_with_false_config_returns_false(self) -> None:
        from navig.core.capability_registry import get_optional, is_enabled
        for name, entry in get_optional().items():
            if entry.config_key:
                parts = entry.config_key.split(".")
                config: dict = {}
                current = config
                for part in parts[:-1]:
                    current[part] = {}
                    current = current[part]
                current[parts[-1]] = False
                assert is_enabled(name, config=config) is False
                break
