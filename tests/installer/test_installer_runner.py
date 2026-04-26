"""Tests for navig.installer.runner — apply() and rollback()."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result
from navig.installer.runner import apply, rollback


def _ctx(dry_run=False) -> InstallerContext:
    return InstallerContext(profile="default", dry_run=dry_run)


def _action(id="a1", module="mock_mod", desc="Test action", reversible=True, placeholder=False) -> Action:
    data: dict[str, Any] = {}
    if placeholder:
        data["placeholder"] = True
    return Action(id=id, description=desc, module=module, data=data, reversible=reversible)


class TestApplyDryRun:
    def test_dry_run_skips_all(self) -> None:
        ctx = _ctx(dry_run=True)
        actions = [_action("a1"), _action("a2")]
        results = apply(actions, ctx)
        assert all(r.state == ModuleState.SKIPPED for r in results)

    def test_dry_run_returns_same_count(self) -> None:
        ctx = _ctx(dry_run=True)
        results = apply([_action("a"), _action("b"), _action("c")], ctx)
        assert len(results) == 3

    def test_dry_run_includes_description(self) -> None:
        ctx = _ctx(dry_run=True)
        results = apply([_action("a", desc="Deploy config")], ctx)
        assert "Deploy config" in results[0].message

    def test_dry_run_action_id_stored(self) -> None:
        ctx = _ctx(dry_run=True)
        results = apply([_action("xyz")], ctx)
        assert results[0].action_id == "xyz"


class TestApplyPlaceholder:
    def test_placeholder_skipped(self) -> None:
        ctx = _ctx()
        results = apply([_action(placeholder=True)], ctx)
        assert results[0].state == ModuleState.SKIPPED

    def test_placeholder_action_id_stored(self) -> None:
        ctx = _ctx()
        results = apply([_action(id="p99", placeholder=True)], ctx)
        assert results[0].action_id == "p99"


class TestApplyRealModule:
    def test_calls_module_apply(self) -> None:
        mock_mod = MagicMock()
        mock_mod.apply.return_value = Result(
            action_id="a1", state=ModuleState.APPLIED, message="done"
        )
        ctx = _ctx()
        action = _action("a1", module="mock_mod")
        with patch("navig.installer.runner.importlib.import_module", return_value=mock_mod):
            results = apply([action], ctx)
        assert results[0].state == ModuleState.APPLIED

    def test_import_error_gives_failed_result(self) -> None:
        ctx = _ctx()
        action = _action("a1", module="nonexistent_module")
        with patch(
            "navig.installer.runner.importlib.import_module",
            side_effect=ImportError("no module"),
        ):
            results = apply([action], ctx)
        assert results[0].state == ModuleState.FAILED
        assert results[0].error is not None

    def test_halts_on_first_failure(self) -> None:
        fail_mod = MagicMock()
        fail_mod.apply.return_value = Result(
            action_id="a1", state=ModuleState.FAILED, message="fail"
        )
        ctx = _ctx()
        actions = [_action("a1"), _action("a2")]
        with patch("navig.installer.runner.importlib.import_module", return_value=fail_mod):
            results = apply(actions, ctx)
        assert len(results) == 1


class TestRollback:
    def test_rollback_calls_module_rollback(self) -> None:
        mock_mod = MagicMock()
        ctx = _ctx()
        action = _action("a1", reversible=True)
        result = Result(action_id="a1", state=ModuleState.APPLIED)
        with patch("navig.installer.runner.importlib.import_module", return_value=mock_mod):
            rollback([action], [result], ctx)
        mock_mod.rollback.assert_called_once()

    def test_rollback_skips_non_reversible(self) -> None:
        mock_mod = MagicMock()
        ctx = _ctx()
        action = _action("a1", reversible=False)
        result = Result(action_id="a1", state=ModuleState.APPLIED)
        with patch("navig.installer.runner.importlib.import_module", return_value=mock_mod):
            rollback([action], [result], ctx)
        mock_mod.rollback.assert_not_called()

    def test_rollback_skips_unnapplied(self) -> None:
        mock_mod = MagicMock()
        ctx = _ctx()
        action = _action(reversible=True)
        result = Result(action_id="a1", state=ModuleState.SKIPPED)
        with patch("navig.installer.runner.importlib.import_module", return_value=mock_mod):
            rollback([action], [result], ctx)
        mock_mod.rollback.assert_not_called()

    def test_rollback_never_raises(self) -> None:
        mock_mod = MagicMock()
        mock_mod.rollback.side_effect = RuntimeError("crash")
        ctx = _ctx()
        action = _action("a1", reversible=True)
        result = Result(action_id="a1", state=ModuleState.APPLIED)
        with patch("navig.installer.runner.importlib.import_module", return_value=mock_mod):
            rollback([action], [result], ctx)  # must NOT raise
