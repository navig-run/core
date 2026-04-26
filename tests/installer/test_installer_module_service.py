"""Hermetic unit tests for navig.installer.modules.service."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import navig.installer.modules.service as svc
from navig.installer.contracts import (
    Action,
    InstallerContext,
    ModuleState,
    Result,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path) -> InstallerContext:
    return InstallerContext(profile="test", config_dir=tmp_path)


def _make_action() -> Action:
    return Action(id="service.install", description="Install daemon service", module="service")


# ---------------------------------------------------------------------------
# module metadata
# ---------------------------------------------------------------------------


class TestModuleMetadata:
    def test_has_name(self):
        assert hasattr(svc, "name")
        assert svc.name == "service"

    def test_has_description(self):
        assert hasattr(svc, "description")
        assert isinstance(svc.description, str)


# ---------------------------------------------------------------------------
# _is_supported
# ---------------------------------------------------------------------------


class TestIsSupported:
    def test_returns_bool(self):
        result = svc._is_supported()
        assert isinstance(result, bool)

    def test_true_on_win32(self):
        with patch.object(sys, "platform", "win32"):
            assert svc._is_supported() is True

    def test_true_on_linux(self):
        with patch.object(sys, "platform", "linux"):
            assert svc._is_supported() is True

    def test_false_on_darwin(self):
        with patch.object(sys, "platform", "darwin"):
            assert svc._is_supported() is False


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


class TestPlan:
    def test_returns_empty_when_unsupported(self, tmp_path):
        ctx = _ctx(tmp_path)
        with patch.object(svc, "_is_supported", return_value=False):
            actions = svc.plan(ctx)
        assert actions == []

    def test_returns_empty_when_already_installed(self, tmp_path):
        ctx = _ctx(tmp_path)
        with (
            patch.object(svc, "_is_supported", return_value=True),
            patch.object(svc, "_service_installed", return_value=True),
        ):
            actions = svc.plan(ctx)
        assert actions == []

    def test_returns_one_action_when_not_installed(self, tmp_path):
        ctx = _ctx(tmp_path)
        with (
            patch.object(svc, "_is_supported", return_value=True),
            patch.object(svc, "_service_installed", return_value=False),
        ):
            actions = svc.plan(ctx)
        assert len(actions) == 1
        assert actions[0].id == "service.install"

    def test_dry_run_still_returns_action(self, tmp_path):
        ctx = InstallerContext(profile="test", dry_run=True, config_dir=tmp_path)
        with (
            patch.object(svc, "_is_supported", return_value=True),
            patch.object(svc, "_service_installed", return_value=False),
        ):
            actions = svc.plan(ctx)
        assert len(actions) == 1


# ---------------------------------------------------------------------------
# apply — skipped paths
# ---------------------------------------------------------------------------


class TestApplySkipped:
    def test_skipped_when_unsupported_platform(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _make_action()
        with patch.object(svc, "_is_supported", return_value=False):
            result = svc.apply(action, ctx)
        assert result.state == ModuleState.SKIPPED

    def test_skipped_when_service_manager_missing(self, tmp_path):
        """Patch the import inside apply() via sys.modules."""
        ctx = _ctx(tmp_path)
        action = _make_action()
        with (
            patch.object(svc, "_is_supported", return_value=True),
            patch.dict("sys.modules", {"navig.daemon.service_manager": None}),
        ):
            result = svc.apply(action, ctx)
        assert result.state == ModuleState.SKIPPED


# ---------------------------------------------------------------------------
# apply — success path
# ---------------------------------------------------------------------------


class TestApplySuccess:
    def test_returns_applied_on_success(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _make_action()
        mock_sm = MagicMock()
        mock_sm.install.return_value = (True, "service installed")
        mock_daemon = MagicMock()
        mock_daemon.service_manager = mock_sm
        with (
            patch.object(svc, "_is_supported", return_value=True),
            patch.dict("sys.modules", {"navig.daemon": mock_daemon, "navig.daemon.service_manager": mock_sm}),
        ):
            result = svc.apply(action, ctx)
        assert result.state == ModuleState.APPLIED
        assert result.action_id == "service.install"

    def test_returns_failed_when_install_returns_false(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _make_action()
        mock_sm = MagicMock()
        mock_sm.install.return_value = (False, "error: permission denied")
        mock_daemon = MagicMock()
        mock_daemon.service_manager = mock_sm
        with (
            patch.object(svc, "_is_supported", return_value=True),
            patch.dict("sys.modules", {"navig.daemon": mock_daemon, "navig.daemon.service_manager": mock_sm}),
        ):
            result = svc.apply(action, ctx)
        assert result.state == ModuleState.FAILED

    def test_returns_failed_on_exception(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _make_action()
        mock_sm = MagicMock()
        mock_sm.install.side_effect = RuntimeError("boom")
        mock_daemon = MagicMock()
        mock_daemon.service_manager = mock_sm
        with (
            patch.object(svc, "_is_supported", return_value=True),
            patch.dict("sys.modules", {"navig.daemon": mock_daemon, "navig.daemon.service_manager": mock_sm}),
        ):
            result = svc.apply(action, ctx)
        assert result.state == ModuleState.FAILED
        assert "boom" in result.message


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


class TestRollback:
    def test_rollback_calls_uninstall(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _make_action()
        result = Result(action_id="service.install", state=ModuleState.APPLIED)
        mock_sm = MagicMock()
        mock_daemon = MagicMock()
        mock_daemon.service_manager = mock_sm
        with patch.dict("sys.modules", {"navig.daemon": mock_daemon, "navig.daemon.service_manager": mock_sm}):
            svc.rollback(action, result, ctx)
        mock_sm.uninstall.assert_called_once()

    def test_rollback_ignores_exceptions(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _make_action()
        result = Result(action_id="service.install", state=ModuleState.APPLIED)
        mock_sm = MagicMock()
        mock_sm.uninstall.side_effect = RuntimeError("nope")
        mock_daemon = MagicMock()
        mock_daemon.service_manager = mock_sm
        with patch.dict("sys.modules", {"navig.daemon": mock_daemon, "navig.daemon.service_manager": mock_sm}):
            # Should not raise
            svc.rollback(action, result, ctx)
