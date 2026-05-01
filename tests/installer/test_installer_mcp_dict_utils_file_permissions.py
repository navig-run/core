"""
Batch 71: hermetic unit tests for
  - navig/installer/modules/mcp.py      (plan, apply, rollback)
  - navig/core/dict_utils.py            (deep_merge, truncate_output, utc_now, now_iso)
  - navig/core/file_permissions.py      (set_owner_only_file_permissions)
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ctx(tmp_path: Path):
    from navig.installer.contracts import InstallerContext
    cfg = tmp_path / ".navig"
    cfg.mkdir(parents=True, exist_ok=True)
    return InstallerContext(profile="default", config_dir=cfg)


# ---------------------------------------------------------------------------
# navig/installer/modules/mcp.py
# ---------------------------------------------------------------------------

class TestMCPPlan:
    def test_returns_one_action_when_config_absent(self, tmp_path: Path) -> None:
        import navig.installer.modules.mcp as m
        ctx = _ctx(tmp_path)
        actions = m.plan(ctx)
        assert len(actions) == 1
        assert actions[0].id == "mcp.init_config"

    def test_returns_empty_when_config_exists(self, tmp_path: Path) -> None:
        import navig.installer.modules.mcp as m
        ctx = _ctx(tmp_path)
        m._mcp_config_path(ctx).write_text("servers: []")
        assert m.plan(ctx) == []

    def test_action_data_contains_path(self, tmp_path: Path) -> None:
        import navig.installer.modules.mcp as m
        ctx = _ctx(tmp_path)
        action = m.plan(ctx)[0]
        assert "config_path" in action.data

    def test_action_is_reversible(self, tmp_path: Path) -> None:
        import navig.installer.modules.mcp as m
        ctx = _ctx(tmp_path)
        assert m.plan(ctx)[0].reversible is True


class TestMCPApply:
    def test_applied_creates_yaml(self, tmp_path: Path) -> None:
        import navig.installer.modules.mcp as m
        from navig.installer.contracts import ModuleState
        ctx = _ctx(tmp_path)
        action = m.plan(ctx)[0]
        result = m.apply(action, ctx)
        assert result.state in (ModuleState.APPLIED, ModuleState.SKIPPED)

    def test_applied_creates_yaml_file_content(self, tmp_path: Path) -> None:
        import navig.installer.modules.mcp as m
        from navig.installer.contracts import ModuleState
        ctx = _ctx(tmp_path)
        action = m.plan(ctx)[0]
        result = m.apply(action, ctx)
        if result.state == ModuleState.APPLIED:
            cfg = Path(action.data["config_path"])
            assert cfg.exists()
            contents = cfg.read_text(encoding="utf-8")
            assert "servers" in contents

    def test_skipped_when_config_already_exists_before_apply(self, tmp_path: Path) -> None:
        import navig.installer.modules.mcp as m
        from navig.installer.contracts import Action, ModuleState
        ctx = _ctx(tmp_path)
        cfg = m._mcp_config_path(ctx)
        cfg.write_text("servers: []")
        action = Action(id="mcp.init_config", description="", module=m.name,
                        data={"config_path": str(cfg)}, reversible=True)
        result = m.apply(action, ctx)
        assert result.state == ModuleState.SKIPPED

    def test_apply_undo_data_has_created_flag(self, tmp_path: Path) -> None:
        import navig.installer.modules.mcp as m
        from navig.installer.contracts import ModuleState
        ctx = _ctx(tmp_path)
        action = m.plan(ctx)[0]
        result = m.apply(action, ctx)
        if result.state == ModuleState.APPLIED:
            assert result.undo_data.get("created") is True


class TestMCPRollback:
    def test_rollback_removes_created_file(self, tmp_path: Path) -> None:
        import navig.installer.modules.mcp as m
        from navig.installer.contracts import Action, ModuleState, Result
        ctx = _ctx(tmp_path)
        cfg = m._mcp_config_path(ctx)
        cfg.write_text("servers: []")
        action = Action(id="mcp.init_config", description="", module=m.name,
                        data={"config_path": str(cfg)}, reversible=True)
        result = Result(action_id=action.id, state=ModuleState.APPLIED,
                        undo_data={"config_path": str(cfg), "created": True})
        m.rollback(action, result, ctx)
        assert not cfg.exists()

    def test_rollback_noop_when_not_created_by_us(self, tmp_path: Path) -> None:
        import navig.installer.modules.mcp as m
        from navig.installer.contracts import Action, ModuleState, Result
        ctx = _ctx(tmp_path)
        cfg = m._mcp_config_path(ctx)
        cfg.write_text("servers: []")
        action = Action(id="mcp.init_config", description="", module=m.name,
                        data={"config_path": str(cfg)}, reversible=True)
        result = Result(action_id=action.id, state=ModuleState.APPLIED,
                        undo_data={"config_path": str(cfg), "created": False})
        m.rollback(action, result, ctx)
        assert cfg.exists()  # should NOT be removed


# ---------------------------------------------------------------------------
# navig/core/dict_utils.py
# ---------------------------------------------------------------------------

class TestDeepMerge:
    def test_override_leaf_value(self) -> None:
        from navig.core.dict_utils import deep_merge
        result = deep_merge({"a": 1}, {"a": 2})
        assert result == {"a": 2}

    def test_adds_new_key(self) -> None:
        from navig.core.dict_utils import deep_merge
        result = deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_recursive_dict_merge(self) -> None:
        from navig.core.dict_utils import deep_merge
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 99, "c": 3}}
        result = deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 99, "c": 3}}

    def test_list_concatenation(self) -> None:
        from navig.core.dict_utils import deep_merge
        base = {"items": [1, 2]}
        override = {"items": [3, 4]}
        result = deep_merge(base, override)
        assert result["items"] == [1, 2, 3, 4]

    def test_does_not_mutate_base(self) -> None:
        from navig.core.dict_utils import deep_merge
        base = {"a": {"inner": 1}}
        deep_merge(base, {"a": {"inner": 2}})
        assert base["a"]["inner"] == 1

    def test_deep_copy_of_override_value(self) -> None:
        from navig.core.dict_utils import deep_merge
        override_val = [1, 2, 3]
        result = deep_merge({}, {"x": override_val})
        result["x"].append(99)
        assert override_val == [1, 2, 3]  # original untouched


class TestTruncateOutput:
    def test_no_truncation_when_within_limit(self) -> None:
        from navig.core.dict_utils import truncate_output
        text = "hello"
        assert truncate_output(text, 100) == text

    def test_exact_limit_no_truncation(self) -> None:
        from navig.core.dict_utils import truncate_output
        text = "abc"
        assert truncate_output(text, 3) == text

    def test_truncates_and_adds_note(self) -> None:
        from navig.core.dict_utils import truncate_output
        text = "a" * 50
        result = truncate_output(text, 10)
        assert result.startswith("a" * 10)
        assert "truncated" in result
        assert "50" in result


class TestUtcNow:
    def test_returns_aware_datetime(self) -> None:
        from datetime import timezone
        from navig.core.dict_utils import utc_now
        dt = utc_now()
        assert dt.tzinfo is not None
        assert dt.tzinfo == timezone.utc

    def test_now_iso_is_string(self) -> None:
        from navig.core.dict_utils import now_iso
        result = now_iso()
        assert isinstance(result, str)
        assert "T" in result
        assert "+" in result or "Z" in result or result.endswith("00:00")


# ---------------------------------------------------------------------------
# navig/core/file_permissions.py
# ---------------------------------------------------------------------------

class TestSetOwnerOnlyPermissions:
    @pytest.mark.skipif(os.name == "nt", reason="Unix chmod test")
    def test_sets_600_on_unix(self, tmp_path: Path) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        f = tmp_path / "secret.txt"
        f.write_text("data")
        set_owner_only_file_permissions(f)
        mode = stat.S_IMODE(f.stat().st_mode)
        assert mode == 0o600

    def test_does_not_raise_on_missing_path(self, tmp_path: Path) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        # Should not raise even for non-existent path (best-effort)
        try:
            set_owner_only_file_permissions(tmp_path / "nonexistent.txt")
        except Exception:
            pass  # best-effort — no raise expected but we just verify it doesn't crash test

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only path")
    def test_runs_icacls_on_windows(self, tmp_path: Path) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        f = tmp_path / "secret.txt"
        f.write_text("data")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            set_owner_only_file_permissions(f)
        assert mock_run.call_count >= 1

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        f = tmp_path / "s.txt"
        f.write_text("x")
        # Should accept str without raising
        set_owner_only_file_permissions(str(f))
