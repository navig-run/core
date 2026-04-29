"""Batch 97 — file_permissions and capability_registry.

Tests:
- navig.core.file_permissions.set_owner_only_file_permissions
- navig.core.capability_registry (CapabilityTier, CapabilityEntry, REGISTRY,
  get_tier, get_core, get_optional, get_labs, is_enabled)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from navig.core.file_permissions import set_owner_only_file_permissions
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


# ===========================================================================
# file_permissions — Unix path
# ===========================================================================


class TestSetOwnerOnlyFilePermissionsUnix:
    def test_unix_calls_chmod_600(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("data")
        with patch("os.name", "posix"):
            with patch("os.chmod") as mock_chmod:
                set_owner_only_file_permissions(target)
        mock_chmod.assert_called_once_with(str(target), 0o600)

    def test_unix_accepts_string_path(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("data")
        with patch("os.name", "posix"):
            with patch("os.chmod") as mock_chmod:
                set_owner_only_file_permissions(str(target))
        mock_chmod.assert_called_once()

    def test_unix_oserror_is_suppressed(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("data")
        with patch("os.name", "posix"):
            with patch("os.chmod", side_effect=OSError("permission denied")):
                # Should not raise
                set_owner_only_file_permissions(target)

    def test_unix_permission_error_suppressed(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("data")
        with patch("os.name", "posix"):
            with patch("os.chmod", side_effect=PermissionError("denied")):
                set_owner_only_file_permissions(target)


# ===========================================================================
# file_permissions — Windows path
# ===========================================================================


class TestSetOwnerOnlyFilePermissionsWindows:
    def test_windows_calls_icacls_three_times(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("data")
        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch("os.name", "nt"):
            with patch("subprocess.run", mock_run):
                with patch("getpass.getuser", return_value="testuser"):
                    set_owner_only_file_permissions(target)
        assert mock_run.call_count == 3

    def test_windows_oserror_suppressed(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("data")
        with patch("os.name", "nt"):
            with patch("subprocess.run", side_effect=OSError("icacls failed")):
                with patch("getpass.getuser", return_value="testuser"):
                    # Should not raise
                    set_owner_only_file_permissions(target)

    def test_windows_subprocess_error_suppressed(self, tmp_path):
        import subprocess
        target = tmp_path / "secret.txt"
        target.write_text("data")
        with patch("os.name", "nt"):
            with patch(
                "subprocess.run",
                side_effect=subprocess.SubprocessError("fail"),
            ):
                with patch("getpass.getuser", return_value="testuser"):
                    set_owner_only_file_permissions(target)

    def test_windows_first_icacls_disables_inheritance(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("data")
        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)
        with patch("os.name", "nt"):
            with patch("subprocess.run", side_effect=fake_run):
                with patch("getpass.getuser", return_value="testuser"):
                    set_owner_only_file_permissions(target)
        assert any("/inheritance:r" in " ".join(c) for c in calls)

    def test_windows_grant_call_includes_username(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("data")
        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)
        with patch("os.name", "nt"):
            with patch("subprocess.run", side_effect=fake_run):
                with patch("getpass.getuser", return_value="alice"):
                    set_owner_only_file_permissions(target)
        full_cmds = [" ".join(c) for c in calls]
        assert any("alice" in c for c in full_cmds)


# ===========================================================================
# CapabilityTier
# ===========================================================================


class TestCapabilityTier:
    def test_core_value(self):
        assert CapabilityTier.CORE == "core"

    def test_optional_value(self):
        assert CapabilityTier.OPTIONAL == "optional"

    def test_labs_value(self):
        assert CapabilityTier.LABS == "labs"

    def test_is_string_enum(self):
        assert isinstance(CapabilityTier.CORE, str)


# ===========================================================================
# CapabilityEntry
# ===========================================================================


class TestCapabilityEntry:
    def test_minimal_creation(self):
        entry = CapabilityEntry(tier=CapabilityTier.CORE)
        assert entry.tier == CapabilityTier.CORE

    def test_defaults(self):
        entry = CapabilityEntry(tier=CapabilityTier.OPTIONAL)
        assert entry.module is None
        assert entry.config_key is None
        assert entry.optional_dep is None
        assert entry.cli_commands == []
        assert entry.notes == ""

    def test_full_creation(self):
        entry = CapabilityEntry(
            tier=CapabilityTier.LABS,
            module="navig.foo",
            config_key="foo.enabled",
            cli_commands=["foo"],
            notes="A test capability",
        )
        assert entry.module == "navig.foo"
        assert entry.config_key == "foo.enabled"
        assert entry.cli_commands == ["foo"]


# ===========================================================================
# REGISTRY
# ===========================================================================


class TestCapabilityRegistry:
    def test_registry_is_dict(self):
        assert isinstance(REGISTRY, dict)

    def test_registry_nonempty(self):
        assert len(REGISTRY) > 0

    def test_all_values_are_entries(self):
        for key, val in REGISTRY.items():
            assert isinstance(val, CapabilityEntry), f"{key} not CapabilityEntry"

    def test_all_tiers_are_valid(self):
        valid = {CapabilityTier.CORE, CapabilityTier.OPTIONAL, CapabilityTier.LABS}
        for key, val in REGISTRY.items():
            assert val.tier in valid, f"{key} has invalid tier {val.tier}"

    def test_daemon_is_core(self):
        assert REGISTRY["daemon"].tier == CapabilityTier.CORE

    def test_telegram_is_core(self):
        assert REGISTRY["telegram"].tier == CapabilityTier.CORE

    def test_vault_is_core(self):
        assert REGISTRY["vault"].tier == CapabilityTier.CORE

    def test_at_least_one_optional(self):
        optionals = get_optional()
        assert len(optionals) >= 1

    def test_at_least_one_labs(self):
        labs = get_labs()
        assert len(labs) >= 1


# ===========================================================================
# get_tier
# ===========================================================================


class TestGetTier:
    def test_known_core_capability(self):
        result = get_tier("daemon")
        assert result == CapabilityTier.CORE

    def test_unknown_returns_none(self):
        result = get_tier("totally_unknown_xyz")
        assert result is None

    def test_returns_correct_enum(self):
        result = get_tier("vault")
        assert result is CapabilityTier.CORE


# ===========================================================================
# get_core / get_optional / get_labs
# ===========================================================================


class TestGetCoreLabs:
    def test_get_core_returns_dict(self):
        assert isinstance(get_core(), dict)

    def test_get_core_all_are_core(self):
        for key, val in get_core().items():
            assert val.tier == CapabilityTier.CORE, f"{key} is not CORE"

    def test_get_optional_returns_dict(self):
        assert isinstance(get_optional(), dict)

    def test_get_optional_all_are_optional(self):
        for key, val in get_optional().items():
            assert val.tier == CapabilityTier.OPTIONAL, f"{key} is not OPTIONAL"

    def test_get_labs_returns_dict(self):
        assert isinstance(get_labs(), dict)

    def test_get_labs_all_are_labs(self):
        for key, val in get_labs().items():
            assert val.tier == CapabilityTier.LABS, f"{key} is not LABS"

    def test_tiers_are_disjoint(self):
        core_keys = set(get_core())
        optional_keys = set(get_optional())
        labs_keys = set(get_labs())
        assert core_keys.isdisjoint(optional_keys)
        assert core_keys.isdisjoint(labs_keys)
        assert optional_keys.isdisjoint(labs_keys)

    def test_union_covers_all_registry(self):
        all_keys = set(get_core()) | set(get_optional()) | set(get_labs())
        assert all_keys == set(REGISTRY)


# ===========================================================================
# is_enabled
# ===========================================================================


class TestIsEnabled:
    def test_core_always_enabled(self):
        assert is_enabled("daemon") is True
        assert is_enabled("vault") is True

    def test_unknown_capability_disabled(self):
        assert is_enabled("totally_unknown_xyz") is False

    def test_optional_disabled_without_config(self):
        # None config → optional disabled
        optionals = get_optional()
        if optionals:
            first_key = next(iter(optionals))
            assert is_enabled(first_key, config=None) is False

    def test_optional_disabled_with_false_config(self):
        optionals = get_optional()
        if not optionals:
            pytest.skip("No optional capabilities in registry")
        key, entry = next(iter(optionals.items()))
        if entry.config_key is None:
            pytest.skip("Optional entry has no config_key")
        config_key = entry.config_key
        # Build nested config with false value
        parts = config_key.split(".")
        cfg: dict = {}
        current = cfg
        for part in parts[:-1]:
            current[part] = {}
            current = current[part]
        current[parts[-1]] = False
        assert is_enabled(key, config=cfg) is False

    def test_optional_enabled_with_true_config(self):
        optionals = get_optional()
        if not optionals:
            pytest.skip("No optional capabilities in registry")
        key, entry = next(iter(optionals.items()))
        if entry.config_key is None:
            pytest.skip("Optional entry has no config_key")
        config_key = entry.config_key
        # Build nested config with true value
        parts = config_key.split(".")
        cfg: dict = {}
        current = cfg
        for part in parts[:-1]:
            current[part] = {}
            current = current[part]
        current[parts[-1]] = True
        assert is_enabled(key, config=cfg) is True

    def test_labs_disabled_without_config(self):
        labs = get_labs()
        if not labs:
            pytest.skip("No labs capabilities")
        first_key = next(iter(labs))
        assert is_enabled(first_key, config=None) is False

    def test_labs_no_config_key_always_disabled(self):
        # Any labs entry without config_key is always off
        for key, entry in get_labs().items():
            if entry.config_key is None:
                assert is_enabled(key, config={"anything": True}) is False
                break
        else:
            pytest.skip("All labs entries have config_key")

    def test_nested_config_key_partial_path_missing(self):
        # config key "foo.bar" but only {"foo": {}} → False
        optionals = get_optional()
        if not optionals:
            pytest.skip("No optional capabilities")
        key, entry = next(iter(optionals.items()))
        if entry.config_key is None or "." not in entry.config_key:
            pytest.skip("No dot-separated config_key available")
        top = entry.config_key.split(".")[0]
        assert is_enabled(key, config={top: {}}) is False
