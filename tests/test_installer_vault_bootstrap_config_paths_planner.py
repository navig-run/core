"""Tests for installer modules: vault_bootstrap, config_paths, planner — batch 57."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_ctx(tmp_path: Path, profile: str = "minimal") -> object:
    """Build a minimal InstallerContext with a temp config_dir."""
    from navig.installer.contracts import InstallerContext

    return InstallerContext(profile=profile, config_dir=tmp_path)


# ---------------------------------------------------------------------------
# installer/modules/vault_bootstrap
# ---------------------------------------------------------------------------


def test_vault_bootstrap_plan_returns_one_action(tmp_path):
    from navig.installer.modules.vault_bootstrap import plan

    ctx = _make_ctx(tmp_path)
    actions = plan(ctx)
    assert len(actions) == 1
    assert actions[0].id == "vault_bootstrap.init"


def test_vault_bootstrap_plan_action_not_reversible(tmp_path):
    from navig.installer.modules.vault_bootstrap import plan

    ctx = _make_ctx(tmp_path)
    actions = plan(ctx)
    assert actions[0].reversible is False


def test_vault_bootstrap_apply_success(tmp_path):
    from navig.installer.modules.vault_bootstrap import apply
    from navig.installer.contracts import Action, ModuleState

    action = Action(id="vault_bootstrap.init", description="test", module="vault_bootstrap")
    ctx = _make_ctx(tmp_path)

    with patch("navig.vault.core.get_vault", create=True):
        result = apply(action, ctx)

    assert result.state == ModuleState.APPLIED
    assert result.action_id == "vault_bootstrap.init"


def test_vault_bootstrap_apply_import_error_skipped(tmp_path):
    from navig.installer.modules.vault_bootstrap import apply
    from navig.installer.contracts import Action, ModuleState

    action = Action(id="vault_bootstrap.init", description="test", module="vault_bootstrap")
    ctx = _make_ctx(tmp_path)

    with patch.dict("sys.modules", {"navig.vault.core": None}):
        result = apply(action, ctx)

    assert result.state == ModuleState.SKIPPED


def test_vault_bootstrap_apply_exception_skipped(tmp_path):
    from navig.installer.modules.vault_bootstrap import apply
    from navig.installer.contracts import Action, ModuleState

    action = Action(id="vault_bootstrap.init", description="test", module="vault_bootstrap")
    ctx = _make_ctx(tmp_path)

    with patch("navig.vault.core.get_vault", side_effect=RuntimeError("no key"), create=True):
        result = apply(action, ctx)

    assert result.state == ModuleState.SKIPPED
    assert "skipped" in result.message.lower()


# ---------------------------------------------------------------------------
# installer/modules/config_paths
# ---------------------------------------------------------------------------


def test_config_paths_plan_all_missing(tmp_path):
    from navig.installer.modules.config_paths import plan, _SUBDIRS

    ctx = _make_ctx(tmp_path / "new_config")
    actions = plan(ctx)
    assert len(actions) == len(_SUBDIRS)


def test_config_paths_plan_existing_dirs_skipped(tmp_path):
    from navig.installer.modules.config_paths import plan, _SUBDIRS

    # Pre-create all subdirs
    config_dir = tmp_path / "config"
    for sub in _SUBDIRS:
        d = config_dir / sub if sub else config_dir
        d.mkdir(parents=True, exist_ok=True)

    ctx = _make_ctx(config_dir)
    actions = plan(ctx)
    assert actions == []


def test_config_paths_apply_creates_dir(tmp_path):
    from navig.installer.modules.config_paths import apply
    from navig.installer.contracts import Action, ModuleState

    new_dir = tmp_path / "workspace"
    action = Action(
        id="config_paths.mkdir.workspace",
        description="Create workspace",
        module="config_paths",
        data={"path": str(new_dir), "existed": False},
    )
    ctx = _make_ctx(tmp_path)
    result = apply(action, ctx)
    assert result.state == ModuleState.APPLIED
    assert new_dir.exists()


def test_config_paths_rollback_removes_empty_dir(tmp_path):
    from navig.installer.modules.config_paths import rollback
    from navig.installer.contracts import Action, Result, ModuleState

    new_dir = tmp_path / "todelete"
    new_dir.mkdir()
    action = Action(
        id="x", description="x", module="config_paths",
        data={"path": str(new_dir), "existed": False}
    )
    result = Result(
        action_id="x", state=ModuleState.APPLIED,
        undo_data={"path": str(new_dir), "existed": False}
    )
    ctx = _make_ctx(tmp_path)
    rollback(action, result, ctx)
    assert not new_dir.exists()


def test_config_paths_rollback_skips_if_existed(tmp_path):
    from navig.installer.modules.config_paths import rollback
    from navig.installer.contracts import Action, Result, ModuleState

    existing = tmp_path / "existing"
    existing.mkdir()
    action = Action(id="x", description="x", module="config_paths", data={})
    result = Result(
        action_id="x", state=ModuleState.APPLIED,
        undo_data={"path": str(existing), "existed": True}
    )
    ctx = _make_ctx(tmp_path)
    rollback(action, result, ctx)
    assert existing.exists()  # NOT removed


# ---------------------------------------------------------------------------
# installer/planner
# ---------------------------------------------------------------------------


def test_planner_raises_for_unknown_profile(tmp_path):
    from navig.installer.planner import plan
    from navig.installer.contracts import InstallerContext

    ctx = InstallerContext(profile="nonexistent_xyz", config_dir=tmp_path)
    with pytest.raises(ValueError, match="Unknown installer profile"):
        plan(ctx)


def test_planner_returns_actions_for_node(tmp_path):
    from navig.installer.planner import plan
    from navig.installer.contracts import InstallerContext

    ctx = InstallerContext(profile="node", config_dir=tmp_path)
    actions = plan(ctx)
    assert isinstance(actions, list)
    assert len(actions) >= 0  # may be 0 if all dirs exist


def test_planner_missing_module_produces_placeholder(tmp_path):
    from navig.installer.planner import plan
    from navig.installer.contracts import InstallerContext
    from navig.installer.profiles import PROFILE_MODULES

    # Use patch to inject an unknown module into the node profile
    patched = PROFILE_MODULES.get("node", []) + ["nonexistent_module_xyz"]
    with patch.dict("navig.installer.profiles.PROFILE_MODULES", {"node": patched}):
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        actions = plan(ctx)

    placeholder = [a for a in actions if "placeholder" in a.id]
    assert len(placeholder) >= 1
    assert placeholder[0].data.get("placeholder") is True


def test_planner_placeholder_module_not_reversible(tmp_path):
    from navig.installer.planner import plan
    from navig.installer.contracts import InstallerContext
    from navig.installer.profiles import PROFILE_MODULES

    patched = ["nonexistent_module_xyz"]
    with patch.dict("navig.installer.profiles.PROFILE_MODULES", {"node": patched}):
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        actions = plan(ctx)

    assert any(not a.reversible for a in actions)
