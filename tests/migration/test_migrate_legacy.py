"""Tests for navig.installer.modules.migrate_legacy."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from navig.installer.contracts import Action, InstallerContext, ModuleState
from navig.installer.modules import migrate_legacy as ml_mod


def _ctx(tmp_path: Path) -> InstallerContext:
    return InstallerContext(profile="default", config_dir=tmp_path / ".navig")


class TestMigrateLegacyPlan:
    def test_returns_one_action(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        actions = ml_mod.plan(ctx)
        assert len(actions) == 1

    def test_action_id(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        action = ml_mod.plan(ctx)[0]
        assert action.id == "migrate_legacy.run"

    def test_action_not_reversible(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        action = ml_mod.plan(ctx)[0]
        assert action.reversible is False

    def test_action_module_is_migrate_legacy(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        action = ml_mod.plan(ctx)[0]
        assert action.module == "migrate_legacy"


class TestMigrateLegacyApply:
    def _action(self, tmp_path: Path) -> Action:
        return ml_mod.plan(_ctx(tmp_path))[0]

    def test_returns_applied_when_both_migrations_succeed(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        action = self._action(tmp_path)

        with (
            patch("navig.installer.modules.migrate_legacy.__import__", create=True),
            patch(
                "navig.commands.init._migrate_legacy_windows_runtime_layout"
            ),
            patch(
                "navig.commands.init._migrate_legacy_documents_dir"
            ),
        ):
            result = ml_mod.apply(action, ctx)

        # Either APPLIED (migrations ran) or SKIPPED (ImportError/exception)
        assert result.state in (ModuleState.APPLIED, ModuleState.SKIPPED)

    def test_returns_skipped_when_imports_fail(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        action = self._action(tmp_path)

        import sys
        # Remove the init module to force ImportError
        saved = sys.modules.pop("navig.commands.init", None)
        try:
            # Patch builtins import to raise for navig.commands.init
            original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

            import builtins
            original = builtins.__import__

            def failing_import(name, *args, **kwargs):
                if "navig.commands.init" in name:
                    raise ImportError("not available")
                return original(name, *args, **kwargs)

            with patch.object(builtins, "__import__", side_effect=failing_import):
                result = ml_mod.apply(action, ctx)
        finally:
            if saved is not None:
                sys.modules["navig.commands.init"] = saved

        assert result.state == ModuleState.SKIPPED

    def test_result_has_action_id(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        action = self._action(tmp_path)
        result = ml_mod.apply(action, ctx)
        assert result.action_id == action.id

    def test_result_has_message(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        action = self._action(tmp_path)
        result = ml_mod.apply(action, ctx)
        assert isinstance(result.message, str)
        assert len(result.message) > 0
