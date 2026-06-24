"""Tests for navig.installer.modules.service — plan/apply/rollback."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import navig.installer.modules.service as svc_mod
from navig.installer.contracts import InstallerContext, ModuleState


def _ctx() -> InstallerContext:
    return InstallerContext(profile="system_standard")


def _action():
    actions = svc_mod.plan(_ctx())
    if actions:
        return actions[0]
    # Create a minimal action if plan returned empty (used in apply tests)
    from navig.installer.contracts import Action
    return Action(
        id="service.install",
        description="register service",
        module="service",
        data={"platform": "linux"},
        reversible=True,
    )


class TestIsSupported:
    def test_win32_supported(self):
        with patch.object(svc_mod.sys, "platform", "win32"):
            assert svc_mod._is_supported() is True

    def test_linux_supported(self):
        with patch.object(svc_mod.sys, "platform", "linux"):
            assert svc_mod._is_supported() is True

    def test_darwin_not_supported(self):
        with patch.object(svc_mod.sys, "platform", "darwin"):
            assert svc_mod._is_supported() is False


class TestPlan:
    def test_returns_empty_on_unsupported_platform(self):
        with patch.object(svc_mod.sys, "platform", "darwin"):
            assert svc_mod.plan(_ctx()) == []

    def test_returns_empty_when_service_installed(self):
        with patch.object(svc_mod.sys, "platform", "linux"):
            with patch.object(svc_mod, "_service_installed", return_value=True):
                assert svc_mod.plan(_ctx()) == []

    def test_returns_one_action_when_not_installed(self):
        with patch.object(svc_mod.sys, "platform", "linux"):
            with patch.object(svc_mod, "_service_installed", return_value=False):
                actions = svc_mod.plan(_ctx())
        assert len(actions) == 1
        assert actions[0].id == "service.install"

    def test_action_is_reversible(self):
        with patch.object(svc_mod.sys, "platform", "linux"):
            with patch.object(svc_mod, "_service_installed", return_value=False):
                actions = svc_mod.plan(_ctx())
        assert actions[0].reversible is True


class TestApply:
    def test_skipped_on_unsupported_platform(self):
        with patch.object(svc_mod.sys, "platform", "darwin"):
            result = svc_mod.apply(_action(), _ctx())
        assert result.state == ModuleState.SKIPPED

    def test_skipped_when_service_manager_missing(self):
        with patch.object(svc_mod.sys, "platform", "linux"):
            with patch.dict(sys.modules, {"navig.daemon": None, "navig.daemon.service_manager": None}):
                result = svc_mod.apply(_action(), _ctx())
        assert result.state == ModuleState.SKIPPED
        assert "service_manager unavailable" in result.message

    def test_applied_when_install_succeeds(self):
        fake_sm = MagicMock()
        fake_sm.install.return_value = (True, "service registered")
        with patch.object(svc_mod.sys, "platform", "linux"):
            with patch.dict(sys.modules, {"navig.daemon.service_manager": fake_sm}):
                result = svc_mod.apply(_action(), _ctx())
        assert result.state == ModuleState.APPLIED
        assert result.undo_data is not None

    def test_failed_when_install_returns_false(self):
        fake_sm = MagicMock()
        fake_sm.install.return_value = (False, "permission denied")
        with patch.object(svc_mod.sys, "platform", "linux"):
            with patch.dict(sys.modules, {"navig.daemon.service_manager": fake_sm}):
                result = svc_mod.apply(_action(), _ctx())
        assert result.state == ModuleState.FAILED
        assert "permission denied" in result.message

    def test_failed_on_exception(self):
        fake_sm = MagicMock()
        fake_sm.install.side_effect = RuntimeError("crash")
        with patch.object(svc_mod.sys, "platform", "linux"):
            with patch.dict(sys.modules, {"navig.daemon.service_manager": fake_sm}):
                result = svc_mod.apply(_action(), _ctx())
        assert result.state == ModuleState.FAILED
        assert "crash" in result.message


class TestRollback:
    def test_calls_uninstall(self):
        fake_sm = MagicMock()
        action = _action()
        result = MagicMock()
        with patch.dict(sys.modules, {"navig.daemon.service_manager": fake_sm}):
            svc_mod.rollback(action, result, _ctx())
        fake_sm.uninstall.assert_called_once()

    def test_rollback_silences_exception(self):
        fake_sm = MagicMock()
        fake_sm.uninstall.side_effect = RuntimeError("uninstall failed")
        action = _action()
        result = MagicMock()
        with patch.dict(sys.modules, {"navig.daemon.service_manager": fake_sm}):
            # Should not raise
            svc_mod.rollback(action, result, _ctx())
