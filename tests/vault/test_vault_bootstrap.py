"""Tests for navig.installer.modules.vault_bootstrap."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

import navig.installer.modules.vault_bootstrap as vault_mod
from navig.installer.contracts import Action, InstallerContext, ModuleState


def _ctx():
    return InstallerContext(profile="node")


def _action():
    # plan() returns actions; make one directly for apply() tests
    return Action(
        id="vault_bootstrap.init",
        description="Ensure vault key file exists",
        module="vault_bootstrap",
        reversible=False,
    )


class TestVaultBootstrapPlan:
    def test_returns_one_action(self):
        actions = vault_mod.plan(_ctx())
        assert len(actions) == 1

    def test_action_id(self):
        actions = vault_mod.plan(_ctx())
        assert actions[0].id == "vault_bootstrap.init"

    def test_action_not_reversible(self):
        actions = vault_mod.plan(_ctx())
        assert actions[0].reversible is False

    def test_action_module(self):
        actions = vault_mod.plan(_ctx())
        assert actions[0].module == "vault_bootstrap"


class TestVaultBootstrapApply:
    def test_success_when_vault_available(self):
        with patch("navig.installer.modules.vault_bootstrap.get_vault", create=True):
            # Patch the local import inside apply()
            mock_vault = MagicMock()
            with patch.dict("sys.modules", {"navig.vault.core": MagicMock(get_vault=mock_vault)}):
                result = vault_mod.apply(_action(), _ctx())
        assert result.state == ModuleState.APPLIED
        assert "initialised" in result.message.lower()

    def test_skipped_on_import_error(self):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "navig.vault.core":
                raise ImportError("no vault")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = vault_mod.apply(_action(), _ctx())
        assert result.state == ModuleState.SKIPPED
        assert "not available" in result.message.lower()

    def test_skipped_on_runtime_exception(self):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "navig.vault.core":
                mod = MagicMock()
                mod.get_vault.side_effect = RuntimeError("disk full")
                return mod
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = vault_mod.apply(_action(), _ctx())
        assert result.state == ModuleState.SKIPPED

    def test_result_action_id(self):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "navig.vault.core":
                raise ImportError
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = vault_mod.apply(_action(), _ctx())
        assert result.action_id == "vault_bootstrap.init"
