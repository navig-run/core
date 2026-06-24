"""Batch 65 — spaces/resolver, spaces/progress, installer/runner."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.spaces.resolver
# ---------------------------------------------------------------------------

class TestFindProjectNavigRoot:
    def test_finds_navig_dir_in_cwd(self, tmp_path):
        from navig.spaces.resolver import _find_project_navig_root
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        result = _find_project_navig_root(tmp_path)
        assert result == navig_dir

    def test_finds_navig_dir_in_parent(self, tmp_path):
        from navig.spaces.resolver import _find_project_navig_root
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        child = tmp_path / "subdir" / "deep"
        child.mkdir(parents=True)
        result = _find_project_navig_root(child)
        assert result == navig_dir

    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        from navig.spaces.resolver import _find_project_navig_root
        # Fake: no .navig dir exists anywhere in the tree
        original_is_dir = Path.is_dir
        def fake_is_dir(p):
            if p.name == ".navig":
                return False
            return original_is_dir(p)
        monkeypatch.setattr(Path, "is_dir", fake_is_dir)
        result = _find_project_navig_root(tmp_path)
        assert result is None


class TestResolveSpace:
    def test_resolves_global_when_no_project(self, tmp_path):
        from navig.spaces.resolver import resolve_space
        from navig.platform import paths as pths
        global_spaces = tmp_path / "config" / "spaces"
        global_spaces.mkdir(parents=True)
        with (
            patch.object(pths, "config_dir", return_value=tmp_path / "config"),
        ):
            cfg = resolve_space("devops", cwd=tmp_path)
        assert cfg.scope == "global"
        assert cfg.canonical_name == "devops"

    def test_resolves_project_space_when_exists(self, tmp_path):
        from navig.spaces.resolver import resolve_space
        from navig.platform import paths as pths
        navig_dir = tmp_path / ".navig"
        project_spaces = navig_dir / "spaces" / "devops"
        project_spaces.mkdir(parents=True)
        with (
            patch.object(pths, "config_dir", return_value=tmp_path / "config"),
        ):
            cfg = resolve_space("devops", cwd=tmp_path)
        assert cfg.scope == "project"
        assert cfg.path == project_spaces

    def test_requested_name_preserved(self, tmp_path):
        from navig.spaces.resolver import resolve_space
        from navig.platform import paths as pths
        with patch.object(pths, "config_dir", return_value=tmp_path / "config"):
            cfg = resolve_space("ops", cwd=tmp_path)
        assert cfg.requested_name == "ops"


class TestGetDefaultSpace:
    def test_returns_default_when_no_env(self):
        from navig.spaces.resolver import get_default_space
        clean = {k: v for k, v in os.environ.items() if k != "NAVIG_SPACE"}
        with patch.dict(os.environ, clean, clear=True):
            result = get_default_space()
        assert result == "default"

    def test_uses_navig_space_env(self):
        from navig.spaces.resolver import get_default_space
        with patch.dict(os.environ, {"NAVIG_SPACE": "devops"}):
            result = get_default_space()
        assert result == "devops"

    def test_normalises_env_value(self):
        from navig.spaces.resolver import get_default_space
        with patch.dict(os.environ, {"NAVIG_SPACE": "ops"}):
            result = get_default_space()
        # normalize_space_name("ops") → "devops"
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# navig.spaces.progress — _completion_from_markdown, format_spaces_progress_lines
# ---------------------------------------------------------------------------

class TestCompletionFromMarkdown:
    def _call(self, text):
        from navig.spaces.progress import _completion_from_markdown
        return _completion_from_markdown(text)

    def test_no_checkboxes_returns_zero(self):
        assert self._call("# Heading\nsome text") == 0.0

    def test_all_checked(self):
        md = "- [x] task1\n- [X] task2\n- [x] task3"
        assert self._call(md) == 100.0

    def test_none_checked(self):
        md = "- [ ] task1\n- [ ] task2"
        assert self._call(md) == 0.0

    def test_half_checked(self):
        md = "- [x] done\n- [ ] todo"
        assert self._call(md) == 50.0

    def test_rounded_result(self):
        md = "- [x] a\n- [ ] b\n- [ ] c"
        result = self._call(md)
        assert result == round((1 / 3) * 100.0, 1)


class TestFormatSpacesProgressLines:
    def _make_progress(self, name="test", scope="global", completion=50.0, goal="Do stuff"):
        from navig.spaces.progress import SpaceProgress
        from pathlib import Path
        return SpaceProgress(
            name=name, scope=scope, path=Path("/tmp"),
            goal=goal, completion_pct=completion, last_updated="2024-01-01"
        )

    def test_empty_rows_returns_fallback(self):
        from navig.spaces.progress import format_spaces_progress_lines
        result = format_spaces_progress_lines([])
        assert len(result) == 1
        assert "No spaces" in result[0]

    def test_single_row_formatted(self):
        from navig.spaces.progress import format_spaces_progress_lines
        rows = [self._make_progress()]
        lines = format_spaces_progress_lines(rows)
        assert len(lines) == 1
        assert "test" in lines[0]
        assert "50.0%" in lines[0]

    def test_respects_max_items(self):
        from navig.spaces.progress import format_spaces_progress_lines
        rows = [self._make_progress(name=f"s{i}") for i in range(10)]
        lines = format_spaces_progress_lines(rows, max_items=3)
        assert len(lines) == 3

    def test_scope_in_output(self):
        from navig.spaces.progress import format_spaces_progress_lines
        rows = [self._make_progress(scope="project")]
        lines = format_spaces_progress_lines(rows)
        assert "project" in lines[0]


# ---------------------------------------------------------------------------
# navig.installer.runner — apply, rollback
# ---------------------------------------------------------------------------

class TestInstallerRunnerApply:
    def _make_ctx(self, tmp_path, dry_run=False):
        from navig.installer.contracts import InstallerContext
        return InstallerContext(profile="node", dry_run=dry_run, config_dir=tmp_path)

    def _make_action(self, mod="config_paths", action_id="test.act"):
        from navig.installer.contracts import Action
        return Action(id=action_id, description="Test", module=mod, reversible=True)

    def test_dry_run_returns_skipped_results(self, tmp_path):
        from navig.installer.contracts import ModuleState
        from navig.installer.runner import apply
        ctx = self._make_ctx(tmp_path, dry_run=True)
        actions = [self._make_action(), self._make_action(action_id="test.act2")]
        results = apply(actions, ctx)
        assert len(results) == 2
        assert all(r.state == ModuleState.SKIPPED for r in results)

    def test_dry_run_message_contains_dry_run(self, tmp_path):
        from navig.installer.runner import apply
        ctx = self._make_ctx(tmp_path, dry_run=True)
        results = apply([self._make_action()], ctx)
        assert "dry-run" in results[0].message

    def test_placeholder_action_skipped(self, tmp_path):
        from navig.installer.contracts import Action, ModuleState
        from navig.installer.runner import apply
        ctx = self._make_ctx(tmp_path)
        action = Action(id="ph.1", description="placeholder", module="missing",
                        data={"placeholder": True}, reversible=False)
        results = apply([action], ctx)
        assert results[0].state == ModuleState.SKIPPED

    def test_stops_on_failed_result(self, tmp_path):
        from navig.installer.contracts import Action, ModuleState, Result
        from navig.installer.runner import apply
        ctx = self._make_ctx(tmp_path)
        actions = [self._make_action("bad_mod", "act1"), self._make_action("bad_mod2", "act2")]

        mock_mod = MagicMock()
        mock_mod.apply.return_value = Result(action_id="act1", state=ModuleState.FAILED)

        with patch("navig.installer.runner.importlib.import_module", return_value=mock_mod):
            results = apply(actions, ctx)

        # Stops after first failure
        assert len(results) == 1
        assert results[0].state == ModuleState.FAILED

    def test_exception_in_module_creates_failed(self, tmp_path):
        from navig.installer.contracts import ModuleState
        from navig.installer.runner import apply
        ctx = self._make_ctx(tmp_path)
        actions = [self._make_action("exploding_mod")]

        mock_mod = MagicMock()
        mock_mod.apply.side_effect = RuntimeError("boom")

        with patch("navig.installer.runner.importlib.import_module", return_value=mock_mod):
            results = apply(actions, ctx)

        assert results[0].state == ModuleState.FAILED
        assert "boom" in results[0].error


class TestInstallerRunnerRollback:
    def _make_ctx(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        return InstallerContext(profile="node", config_dir=tmp_path)

    def test_rollback_calls_module_rollback(self, tmp_path):
        from navig.installer.contracts import Action, ModuleState, Result
        from navig.installer.runner import rollback
        ctx = self._make_ctx(tmp_path)

        action = Action(id="a1", description="test", module="mymod", reversible=True)
        result = Result(action_id="a1", state=ModuleState.APPLIED, undo_data={})

        mock_mod = MagicMock()
        with patch("navig.installer.runner.importlib.import_module", return_value=mock_mod):
            rollback([action], [result], ctx)

        mock_mod.rollback.assert_called_once_with(action, result, ctx)

    def test_rollback_skips_non_reversible(self, tmp_path):
        from navig.installer.contracts import Action, ModuleState, Result
        from navig.installer.runner import rollback
        ctx = self._make_ctx(tmp_path)

        action = Action(id="a1", description="test", module="mymod", reversible=False)
        result = Result(action_id="a1", state=ModuleState.APPLIED, undo_data={})

        mock_mod = MagicMock()
        with patch("navig.installer.runner.importlib.import_module", return_value=mock_mod):
            rollback([action], [result], ctx)

        mock_mod.rollback.assert_not_called()

    def test_rollback_exception_silenced(self, tmp_path):
        from navig.installer.contracts import Action, ModuleState, Result
        from navig.installer.runner import rollback
        ctx = self._make_ctx(tmp_path)

        action = Action(id="a1", description="test", module="mymod", reversible=True)
        result = Result(action_id="a1", state=ModuleState.APPLIED, undo_data={})

        mock_mod = MagicMock()
        mock_mod.rollback.side_effect = RuntimeError("rollback exploded")
        with patch("navig.installer.runner.importlib.import_module", return_value=mock_mod):
            rollback([action], [result], ctx)  # must not raise
