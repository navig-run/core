"""Tests for navig.installer.modules.config_paths — plan, apply, rollback."""
from __future__ import annotations

from pathlib import Path

import pytest

import navig.installer.modules.config_paths as cp_mod
from navig.installer.contracts import Action, InstallerContext, ModuleState, Result


def _ctx(tmp_path: Path) -> InstallerContext:
    return InstallerContext(profile="node", config_dir=tmp_path)


def _make_action(sub: str, path: Path) -> Action:
    return Action(
        id=f"config_paths.mkdir.{sub or 'root'}",
        description=f"Create {path}",
        module="config_paths",
        data={"path": str(path), "existed": False},
    )


class TestConfigPathsPlan:
    def test_emits_actions_for_missing_dirs(self, tmp_path):
        ctx = _ctx(tmp_path / "navig_home")  # does not exist yet
        actions = cp_mod.plan(ctx)
        assert len(actions) > 0

    def test_no_actions_when_all_dirs_exist(self, tmp_path):
        ctx = _ctx(tmp_path)
        # Create all required subdirs
        for sub in cp_mod._SUBDIRS:
            d = tmp_path / sub if sub else tmp_path
            d.mkdir(parents=True, exist_ok=True)
        actions = cp_mod.plan(ctx)
        assert len(actions) == 0

    def test_action_ids_include_subdir_name(self, tmp_path):
        ctx = _ctx(tmp_path / "fresh")
        actions = cp_mod.plan(ctx)
        ids = [a.id for a in actions]
        assert any("root" in aid for aid in ids)

    def test_actions_are_reversible(self, tmp_path):
        ctx = _ctx(tmp_path / "fresh")
        actions = cp_mod.plan(ctx)
        for a in actions:
            assert a.reversible is True


class TestConfigPathsApply:
    def test_creates_directory(self, tmp_path):
        target = tmp_path / "logs"
        action = _make_action("logs", target)
        result = cp_mod.apply(action, _ctx(tmp_path))
        assert target.exists()
        assert result.state == ModuleState.APPLIED

    def test_ok_when_dir_already_exists(self, tmp_path):
        target = tmp_path / "cache"
        target.mkdir()
        action = _make_action("cache", target)
        result = cp_mod.apply(action, _ctx(tmp_path))
        assert result.state == ModuleState.APPLIED

    def test_result_contains_path(self, tmp_path):
        target = tmp_path / "workspace"
        action = _make_action("workspace", target)
        result = cp_mod.apply(action, _ctx(tmp_path))
        assert str(target) in result.message


class TestConfigPathsRollback:
    def test_removes_empty_dir_when_not_existed(self, tmp_path):
        target = tmp_path / "fresh_dir"
        target.mkdir()
        action = _make_action("fresh_dir", target)
        result = Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            undo_data={"path": str(target), "existed": False},
        )
        cp_mod.rollback(action, result, _ctx(tmp_path))
        assert not target.exists()

    def test_skips_removal_when_dir_existed(self, tmp_path):
        target = tmp_path / "existing_dir"
        target.mkdir()
        action = _make_action("existing_dir", target)
        result = Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            undo_data={"path": str(target), "existed": True},
        )
        cp_mod.rollback(action, result, _ctx(tmp_path))
        assert target.exists()  # should NOT have been removed

    def test_rollback_of_non_empty_dir_is_noop(self, tmp_path):
        target = tmp_path / "nonempty"
        target.mkdir()
        (target / "file.txt").write_text("content")
        action = _make_action("nonempty", target)
        result = Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            undo_data={"path": str(target), "existed": False},
        )
        cp_mod.rollback(action, result, _ctx(tmp_path))  # should not raise
        assert target.exists()
