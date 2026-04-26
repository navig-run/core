"""Tests for navig.installer.modules.shell_integration."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import navig.installer.modules.shell_integration as si_mod
from navig.installer.contracts import Action, InstallerContext, ModuleState, Result


def _ctx() -> InstallerContext:
    return InstallerContext(profile="standard")


def _action(rc_path: str, bin_dir: str) -> Action:
    return Action(
        id=f"shell_integration.{Path(rc_path).name}",
        description="test",
        module="shell_integration",
        data={"rc": rc_path, "bin_dir": bin_dir},
        reversible=True,
    )


class TestPlan:
    def test_returns_empty_on_windows(self):
        with patch.object(si_mod.sys, "platform", "win32"):
            result = si_mod.plan(_ctx())
        assert result == []

    def test_returns_empty_when_no_bin_dir(self):
        with patch.object(si_mod.sys, "platform", "linux"):
            with patch.object(si_mod, "_navig_bin_dir", return_value=None):
                result = si_mod.plan(_ctx())
        assert result == []

    def test_returns_empty_when_rc_already_has_marker(self, tmp_path):
        rc = tmp_path / ".bashrc"
        rc.write_text(f"# navig shell integration\nexport PATH=...\n", encoding="utf-8")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        with patch.object(si_mod.sys, "platform", "linux"):
            with patch.object(si_mod, "_navig_bin_dir", return_value=bin_dir):
                with patch.object(si_mod, "_shell_rc_candidates", return_value=[rc]):
                    result = si_mod.plan(_ctx())
        assert result == []

    def test_returns_action_for_clean_rc(self, tmp_path):
        rc = tmp_path / ".bashrc"
        rc.write_text("# empty rc\n", encoding="utf-8")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        with patch.object(si_mod.sys, "platform", "linux"):
            with patch.object(si_mod, "_navig_bin_dir", return_value=bin_dir):
                with patch.object(si_mod, "_shell_rc_candidates", return_value=[rc]):
                    actions = si_mod.plan(_ctx())
        assert len(actions) == 1
        assert actions[0].data["rc"] == str(rc)


class TestApply:
    def test_appends_snippet_to_rc(self, tmp_path):
        rc = tmp_path / ".bashrc"
        rc.write_text("# existing\n", encoding="utf-8")
        action = _action(str(rc), "/usr/local/bin")
        result = si_mod.apply(action, _ctx())
        assert result.state == ModuleState.APPLIED
        content = rc.read_text(encoding="utf-8")
        assert "navig shell integration" in content
        assert "/usr/local/bin" in content

    def test_undo_data_contains_snippet(self, tmp_path):
        rc = tmp_path / ".bashrc"
        rc.write_text("", encoding="utf-8")
        action = _action(str(rc), "/usr/local/bin")
        result = si_mod.apply(action, _ctx())
        assert "snippet" in result.undo_data
        assert "navig shell integration" in result.undo_data["snippet"]

    def test_returns_failed_on_io_error(self, tmp_path):
        nonexistent_dir = tmp_path / "no_such_dir"
        rc = nonexistent_dir / ".bashrc"
        action = _action(str(rc), "/bin")
        result = si_mod.apply(action, _ctx())
        assert result.state == ModuleState.FAILED


class TestRollback:
    def test_removes_snippet_from_rc(self, tmp_path):
        rc = tmp_path / ".bashrc"
        snippet = "\n# navig shell integration\nexport PATH=\"/bin:$PATH\"\n"
        rc.write_text("# existing\n" + snippet, encoding="utf-8")
        action = _action(str(rc), "/bin")
        result = Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            undo_data={"rc": str(rc), "snippet": snippet},
        )
        si_mod.rollback(action, result, _ctx())
        content = rc.read_text(encoding="utf-8")
        assert "navig shell integration" not in content

    def test_rollback_noop_when_rc_missing(self, tmp_path):
        action = _action(str(tmp_path / "notexist"), "/bin")
        result = Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            undo_data={"rc": str(tmp_path / "notexist"), "snippet": "x"},
        )
        si_mod.rollback(action, result, _ctx())  # no error

    def test_rollback_noop_when_no_undo_data(self, tmp_path):
        action = _action(str(tmp_path / ".bashrc"), "/bin")
        result = Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            undo_data={},
        )
        si_mod.rollback(action, result, _ctx())  # no error
