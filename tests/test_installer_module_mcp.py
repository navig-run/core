"""Hermetic unit tests for navig.installer.modules.mcp."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import navig.installer.modules.mcp as mcp_module
from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path, **kwargs) -> InstallerContext:
    return InstallerContext(profile="test", config_dir=tmp_path, **kwargs)


def _action(tmp_path: Path) -> Action:
    return Action(
        id="mcp.init_config",
        description="mcp: create mcp_servers.yaml",
        module="mcp",
        data={"config_path": str(tmp_path / "mcp_servers.yaml")},
        reversible=True,
    )


# ---------------------------------------------------------------------------
# Module metadata
# ---------------------------------------------------------------------------


class TestModuleMetadata:
    def test_name(self):
        assert mcp_module.name == "mcp"

    def test_description_is_string(self):
        assert isinstance(mcp_module.description, str)
        assert len(mcp_module.description) > 3


# ---------------------------------------------------------------------------
# plan()
# ---------------------------------------------------------------------------


class TestPlan:
    def test_plan_returns_action_when_no_config(self, tmp_path):
        ctx = _ctx(tmp_path)
        actions = mcp_module.plan(ctx)
        assert len(actions) == 1
        assert actions[0].id == "mcp.init_config"

    def test_plan_returns_empty_when_config_exists(self, tmp_path):
        config = tmp_path / "mcp_servers.yaml"
        config.write_text("servers: []", encoding="utf-8")
        ctx = _ctx(tmp_path)
        actions = mcp_module.plan(ctx)
        assert actions == []

    def test_action_is_reversible(self, tmp_path):
        ctx = _ctx(tmp_path)
        actions = mcp_module.plan(ctx)
        assert actions[0].reversible is True


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------


class TestApply:
    def test_apply_creates_config_file(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        result = mcp_module.apply(action, ctx)
        assert (tmp_path / "mcp_servers.yaml").exists()

    def test_apply_returns_applied_state(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        result = mcp_module.apply(action, ctx)
        assert result.state == ModuleState.APPLIED

    def test_apply_skips_if_file_exists(self, tmp_path):
        (tmp_path / "mcp_servers.yaml").write_text("servers: []", encoding="utf-8")
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        result = mcp_module.apply(action, ctx)
        assert result.state == ModuleState.SKIPPED

    def test_apply_returned_result_has_action_id(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        result = mcp_module.apply(action, ctx)
        assert result.action_id == action.id

    def test_apply_config_content_has_servers_key(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        mcp_module.apply(action, ctx)
        content = (tmp_path / "mcp_servers.yaml").read_text(encoding="utf-8")
        assert "servers:" in content

    def test_apply_missing_mcp_manager_returns_skipped(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        with patch.dict("sys.modules", {"navig.mcp_manager": None}):
            # Simulate ImportError path by patching the import
            import builtins
            original_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "navig.mcp_manager":
                    raise ImportError("mcp_manager mocked unavailable")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                result = mcp_module.apply(action, ctx)

        assert result.state == ModuleState.SKIPPED

    def test_apply_undo_data_contains_created_true(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        result = mcp_module.apply(action, ctx)
        assert result.undo_data.get("created") is True


# ---------------------------------------------------------------------------
# rollback()
# ---------------------------------------------------------------------------


class TestRollback:
    def test_rollback_removes_created_file(self, tmp_path):
        config = tmp_path / "mcp_servers.yaml"
        config.write_text("servers: []", encoding="utf-8")
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        result = Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            undo_data={"config_path": str(config), "created": True},
        )
        mcp_module.rollback(action, result, ctx)
        assert not config.exists()

    def test_rollback_does_nothing_if_not_created(self, tmp_path):
        config = tmp_path / "mcp_servers.yaml"
        config.write_text("pre-existing", encoding="utf-8")
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        result = Result(
            action_id=action.id,
            state=ModuleState.SKIPPED,
            undo_data={"config_path": str(config), "created": False},
        )
        mcp_module.rollback(action, result, ctx)
        # File should still exist since we didn't create it
        assert config.exists()

    def test_rollback_tolerates_missing_file(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        result = Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            undo_data={"config_path": str(tmp_path / "nonexistent.yaml"), "created": True},
        )
        # Should not raise
        mcp_module.rollback(action, result, ctx)

    def test_rollback_does_nothing_with_empty_undo_data(self, tmp_path):
        ctx = _ctx(tmp_path)
        action = _action(tmp_path)
        result = Result(action_id=action.id, state=ModuleState.APPLIED)
        mcp_module.rollback(action, result, ctx)  # no error
