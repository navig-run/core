"""
Tests for navig.agent.worktree — WorktreeManager + agent tools.

All git operations are mocked — no actual git repository is required.

FB-05 implementation tests.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from navig.agent.worktree import (
    BRANCH_PREFIX,
    MAX_WORKTREES,
    WORKTREE_DIR,
    Worktree,
    WorktreeManager,
    get_manager,
    reset_manager,
)

pytestmark = pytest.mark.integration

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess:
    """Build a fake CompletedProcess for mocking _run_git."""
    return subprocess.CompletedProcess(
        args="git ...",
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _make_manager(tmp_path: Path) -> WorktreeManager:
    """Return a WorktreeManager pointed at tmp_path."""
    return WorktreeManager(repo_root=tmp_path)


# ─────────────────────────────────────────────────────────────
# Autouse reset singleton
# ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset module-level singleton between tests."""
    reset_manager()
    yield
    reset_manager()


# ─────────────────────────────────────────────────────────────
# Worktree dataclass
# ─────────────────────────────────────────────────────────────


class TestWorktreeDataclass:
    """Tests for the Worktree dataclass."""

    def test_to_dict_has_expected_keys(self, tmp_path):
        wt = Worktree(name="test", path=tmp_path, branch="navig/test")
        d = wt.to_dict()
        assert d["name"] == "test"
        assert d["path"] == str(tmp_path)
        assert d["branch"] == "navig/test"
        assert "age" in d
        assert d["merged"] is False
        assert d["deleted"] is False

    def test_age_seconds_increases(self, tmp_path):
        wt = Worktree(name="x", path=tmp_path, branch="navig/x")
        assert wt.age_seconds >= 0
        assert wt.age_seconds < 2.0

    def test_defaults_are_not_merged_or_deleted(self, tmp_path):
        wt = Worktree(name="a", path=tmp_path, branch="b")
        assert wt.merged is False
        assert wt.deleted is False

    def test_to_dict_age_is_string(self, tmp_path):
        wt = Worktree(name="a", path=tmp_path, branch="b")
        d = wt.to_dict()
        assert isinstance(d["age"], str)
        assert d["age"].endswith("s")


# ─────────────────────────────────────────────────────────────
# WorktreeManager — init
# ─────────────────────────────────────────────────────────────


class TestWorktreeManagerInit:
    """Tests for WorktreeManager initialisation."""

    def test_repo_root_resolved(self, tmp_path):
        mgr = WorktreeManager(repo_root=str(tmp_path))
        assert mgr.repo_root == tmp_path.resolve()

    def test_worktree_base_default(self, tmp_path):
        mgr = WorktreeManager(repo_root=tmp_path)
        assert mgr.worktree_base == tmp_path / WORKTREE_DIR

    def test_custom_worktree_dir(self, tmp_path):
        mgr = WorktreeManager(repo_root=tmp_path, worktree_dir=".wt")
        assert mgr.worktree_base == tmp_path / ".wt"

    def test_active_count_starts_at_zero(self, tmp_path):
        mgr = WorktreeManager(repo_root=tmp_path)
        assert mgr.active_count == 0

    def test_list_worktrees_empty_initially(self, tmp_path):
        mgr = WorktreeManager(repo_root=tmp_path)
        assert mgr.list_worktrees() == []

    def test_repr_contains_repo_root(self, tmp_path):
        mgr = WorktreeManager(repo_root=tmp_path)
        assert "WorktreeManager" in repr(mgr)
        assert "active=0" in repr(mgr)


# ─────────────────────────────────────────────────────────────
# WorktreeManager.create()
# ─────────────────────────────────────────────────────────────


class TestWorktreeManagerCreate:
    """Tests for WorktreeManager.create()."""

    async def test_create_success(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.side_effect = [
                _completed(0, "true"),  # rev-parse check
                _completed(0),  # worktree add
            ]
            wt = await mgr.create("worker-a")

        assert wt.name == "worker-a"
        assert wt.branch == f"{BRANCH_PREFIX}worker-a"
        assert wt.path == mgr.worktree_base / "worker-a"
        assert wt.merged is False
        assert wt.deleted is False

    async def test_create_registers_in_manager(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.side_effect = [_completed(0, "true"), _completed(0)]
            await mgr.create("worker-b")
        assert mgr.active_count == 1
        assert mgr.get_worktree("worker-b") is not None

    async def test_create_invalid_name_empty(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="Invalid worktree name"):
            await mgr.create("")

    async def test_create_invalid_name_special_chars(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="Invalid worktree name"):
            await mgr.create("worker@bad!")

    async def test_create_valid_name_with_hyphens_underscores(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.side_effect = [_completed(0, "true"), _completed(0)]
            wt = await mgr.create("worker-a_1")
        assert wt.name == "worker-a_1"

    async def test_create_duplicate_name_raises(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.side_effect = [_completed(0, "true"), _completed(0)]
            await mgr.create("worker-a")
        with pytest.raises(ValueError, match="already exists"):
            await mgr.create("worker-a")

    async def test_create_not_git_repo_raises(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _completed(128, stderr="not a git repo")
            with pytest.raises(RuntimeError, match="Not a git repository"):
                await mgr.create("worker-a")

    async def test_create_git_failure_raises(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.side_effect = [
                _completed(0, "true"),  # is-inside-work-tree OK
                _completed(128, stderr="branch already exists"),  # worktree add fails
            ]
            with pytest.raises(RuntimeError, match="Failed to create worktree"):
                await mgr.create("worker-a")

    async def test_create_uses_branch_prefix(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.side_effect = [_completed(0, "true"), _completed(0)]
            wt = await mgr.create("myworker")
        assert wt.branch.startswith(BRANCH_PREFIX)

    async def test_create_max_worktrees_raises(self, tmp_path):
        mgr = _make_manager(tmp_path)
        # Fill up to max
        for i in range(MAX_WORKTREES):
            wt = Worktree(name=f"w{i}", path=tmp_path / f"w{i}", branch=f"navig/w{i}")
            mgr._worktrees[f"w{i}"] = wt
        with pytest.raises(RuntimeError, match="Maximum worktree limit"):
            await mgr.create("overflow")

    async def test_create_custom_base_branch(self, tmp_path):
        mgr = _make_manager(tmp_path)
        captured = []

        async def fake_git(cmd: str):
            captured.append(cmd)
            return _completed(0, "true")

        with patch.object(mgr, "_run_git", side_effect=fake_git):
            await mgr.create("worker-c", base_branch="develop")
        # The worktree add command should reference develop
        assert any("develop" in c for c in captured)


# ─────────────────────────────────────────────────────────────
# WorktreeManager.merge_back()
# ─────────────────────────────────────────────────────────────


class TestWorktreeManagerMergeBack:
    """Tests for WorktreeManager.merge_back()."""

    async def test_merge_nonexistent_raises(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="No worktree"):
            await mgr.merge_back("missing")

    async def test_merge_nothing_to_merge(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.side_effect = [
                _completed(0, "main"),  # rev-parse HEAD
                _completed(0, ""),  # git log (empty = nothing to merge)
            ]
            result = await mgr.merge_back("w")
        assert result is True
        assert wt.merged is True

    async def test_merge_success_with_commits(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.side_effect = [
                _completed(0, "main"),  # rev-parse HEAD
                _completed(0, "abc123 commit"),  # git log (has commits)
                _completed(0),  # git merge
            ]
            result = await mgr.merge_back("w")
        assert result is True
        assert wt.merged is True

    async def test_merge_conflict_returns_false(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.side_effect = [
                _completed(0, "main"),  # rev-parse HEAD
                _completed(0, "abc123 commit"),  # git log
                _completed(1, stderr="CONFLICT"),  # git merge fails
                _completed(0),  # git merge --abort
            ]
            result = await mgr.merge_back("w")
        assert result is False
        assert wt.merged is False

    async def test_merge_already_merged_is_idempotent(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w", merged=True)
        mgr._worktrees["w"] = wt
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            result = await mgr.merge_back("w")
        assert result is True
        mock_git.assert_not_called()

    async def test_merge_with_explicit_target(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        captured = []

        async def fake_git(cmd: str):
            captured.append(cmd)
            return _completed(0, "")

        with patch.object(mgr, "_run_git", side_effect=fake_git):
            result = await mgr.merge_back("w", target_branch="feature-x")
        assert result is True
        # Should NOT call rev-parse (target given explicitly)
        assert not any("rev-parse" in c for c in captured)


# ─────────────────────────────────────────────────────────────
# WorktreeManager.remove()
# ─────────────────────────────────────────────────────────────


class TestWorktreeManagerRemove:
    """Tests for WorktreeManager.remove()."""

    async def test_remove_success(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _completed(0)
            await mgr.remove("w")
        assert "w" not in mgr._worktrees
        assert wt.deleted is True

    async def test_remove_nonexistent_is_idempotent(self, tmp_path):
        mgr = _make_manager(tmp_path)
        # Should not raise
        await mgr.remove("does-not-exist")

    async def test_remove_decrements_active_count(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        assert mgr.active_count == 1
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _completed(0)
            await mgr.remove("w")
        assert mgr.active_count == 0

    async def test_remove_force_flag_passed(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        captured = []

        async def fake_git(cmd: str):
            captured.append(cmd)
            return _completed(0)

        with patch.object(mgr, "_run_git", side_effect=fake_git):
            with patch("os.name", "posix"):
                await mgr.remove("w", force=True)
        # The remove command should contain --force
        assert any("--force" in c for c in captured)

    async def test_remove_without_force_no_flag(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        captured = []

        async def fake_git(cmd: str):
            captured.append(cmd)
            return _completed(0)

        with patch.object(mgr, "_run_git", side_effect=fake_git):
            with patch("os.name", "posix"):
                await mgr.remove("w", force=False)
        remove_cmds = [c for c in captured if "worktree remove" in c]
        assert remove_cmds
        assert "--force" not in remove_cmds[0]

    async def test_remove_windows_fallback_shutil(self, tmp_path):
        """When all Windows retries fail, falls back to shutil.rmtree."""
        mgr = _make_manager(tmp_path)
        wt_path = tmp_path / "w"
        wt_path.mkdir()
        wt = Worktree(name="w", path=wt_path, branch="navig/w")
        mgr._worktrees["w"] = wt

        async def fake_git(cmd: str):
            if "worktree remove" in cmd:
                return _completed(1, stderr="locked")
            return _completed(0)

        with patch("navig.agent.worktree.os.name", "nt"):
            with patch("navig.agent.worktree.WINDOWS_RETRY_ATTEMPTS", 1):
                with patch("navig.agent.worktree.WINDOWS_RETRY_DELAY", 0):
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        with patch("shutil.rmtree") as mock_rmtree:
                            with patch.object(mgr, "_run_git", side_effect=fake_git):
                                await mgr.remove("w", force=True)
        mock_rmtree.assert_called_once()


# ─────────────────────────────────────────────────────────────
# WorktreeManager.cleanup_all()
# ─────────────────────────────────────────────────────────────


class TestWorktreeManagerCleanupAll:
    """Tests for WorktreeManager.cleanup_all()."""

    async def test_cleanup_empty_returns_zero(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _completed(0)
            count = await mgr.cleanup_all()
        assert count == 0

    async def test_cleanup_removes_all_worktrees(self, tmp_path):
        mgr = _make_manager(tmp_path)
        for i in range(3):
            wt = Worktree(name=f"w{i}", path=tmp_path / f"w{i}", branch=f"navig/w{i}")
            mgr._worktrees[f"w{i}"] = wt
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _completed(0)
            count = await mgr.cleanup_all()
        assert count == 3
        assert mgr.active_count == 0

    async def test_cleanup_prunes_git_worktrees(self, tmp_path):
        mgr = _make_manager(tmp_path)
        captured = []

        async def fake_git(cmd: str):
            captured.append(cmd)
            return _completed(0)

        with patch.object(mgr, "_run_git", side_effect=fake_git):
            await mgr.cleanup_all()
        assert any("worktree prune" in c for c in captured)

    async def test_cleanup_removes_empty_worktree_dir(self, tmp_path):
        mgr = _make_manager(tmp_path)
        # Create the worktree base dir (empty)
        mgr.worktree_base.mkdir()
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _completed(0)
            await mgr.cleanup_all()
        assert not mgr.worktree_base.exists()


# ─────────────────────────────────────────────────────────────
# WorktreeManager.list_worktrees() / get_worktree()
# ─────────────────────────────────────────────────────────────


class TestWorktreeManagerList:
    """Tests for list and get operations."""

    def test_list_returns_only_active(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt_active = Worktree(name="a", path=tmp_path / "a", branch="navig/a")
        wt_deleted = Worktree(name="b", path=tmp_path / "b", branch="navig/b", deleted=True)
        mgr._worktrees["a"] = wt_active
        mgr._worktrees["b"] = wt_deleted
        listing = mgr.list_worktrees()
        assert len(listing) == 1
        assert listing[0]["name"] == "a"

    def test_get_worktree_found(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="x", path=tmp_path / "x", branch="navig/x")
        mgr._worktrees["x"] = wt
        assert mgr.get_worktree("x") is wt

    def test_get_worktree_not_found(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.get_worktree("missing") is None

    def test_get_worktree_deleted_returns_none(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="d", path=tmp_path / "d", branch="navig/d", deleted=True)
        mgr._worktrees["d"] = wt
        assert mgr.get_worktree("d") is None

    def test_active_count_excludes_deleted(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._worktrees["a"] = Worktree(name="a", path=tmp_path / "a", branch="navig/a")
        mgr._worktrees["b"] = Worktree(
            name="b", path=tmp_path / "b", branch="navig/b", deleted=True
        )
        assert mgr.active_count == 1


# ─────────────────────────────────────────────────────────────
# Context manager
# ─────────────────────────────────────────────────────────────


class TestContextManager:
    """Tests for the async context manager protocol."""

    async def test_context_manager_calls_cleanup_all(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _completed(0)
            async with mgr:
                assert mgr.active_count == 1
        assert mgr.active_count == 0

    async def test_context_manager_cleans_up_on_exception(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _completed(0)
            with pytest.raises(RuntimeError):
                async with mgr:
                    raise RuntimeError("test error")
        assert mgr.active_count == 0


# ─────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────


class TestSingleton:
    """Tests for get_manager() / reset_manager()."""

    def test_get_manager_requires_repo_root_first_call(self):
        with pytest.raises(ValueError, match="repo_root is required"):
            get_manager()

    def test_get_manager_returns_same_instance(self, tmp_path):
        m1 = get_manager(repo_root=tmp_path)
        m2 = get_manager()  # second call — no repo_root needed
        assert m1 is m2

    def test_reset_manager_clears_singleton(self, tmp_path):
        get_manager(repo_root=tmp_path)
        reset_manager()
        with pytest.raises(ValueError):
            get_manager()

    def test_get_manager_after_reset_needs_repo_root(self, tmp_path):
        get_manager(repo_root=tmp_path)
        reset_manager()
        m = get_manager(repo_root=tmp_path)
        assert m is not None


# ─────────────────────────────────────────────────────────────
# Agent tools — WorktreeCreateTool
# ─────────────────────────────────────────────────────────────


class TestWorktreeCreateTool:
    """Tests for WorktreeCreateTool."""

    @pytest.fixture
    def tool(self):
        from navig.agent.tools.worktree_tools import WorktreeCreateTool

        return WorktreeCreateTool()

    @pytest.fixture
    def mock_mgr(self, tmp_path):
        mgr = _make_manager(tmp_path)
        return mgr

    async def test_create_success(self, tool, tmp_path):
        wt = Worktree(name="w1", path=tmp_path / "w1", branch="navig/w1")
        mock_manager = AsyncMock()
        mock_manager.create.return_value = wt
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({"name": "w1"})
        assert result.success is True
        data = json.loads(result.output)
        assert data["name"] == "w1"

    async def test_create_missing_name(self, tool):
        result = await tool.run({})
        assert result.success is False
        assert "name" in result.error

    async def test_create_empty_name(self, tool):
        result = await tool.run({"name": "  "})
        assert result.success is False

    async def test_create_propagates_value_error(self, tool):
        mock_manager = AsyncMock()
        mock_manager.create.side_effect = ValueError("already exists")
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({"name": "dup"})
        assert result.success is False
        assert "already exists" in result.error

    async def test_create_propagates_runtime_error(self, tool):
        mock_manager = AsyncMock()
        mock_manager.create.side_effect = RuntimeError("git failure")
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({"name": "bad"})
        assert result.success is False

    async def test_create_passes_base_branch(self, tool, tmp_path):
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mock_manager = AsyncMock()
        mock_manager.create.return_value = wt
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            await tool.run({"name": "w", "base_branch": "develop"})
        mock_manager.create.assert_awaited_once_with(name="w", base_branch="develop")

    def test_tool_name(self, tool):
        assert tool.name == "worktree_create"

    def test_tool_has_parameters(self, tool):
        assert len(tool.parameters) >= 1
        names = [p["name"] for p in tool.parameters]
        assert "name" in names


# ─────────────────────────────────────────────────────────────
# Agent tools — WorktreeListTool
# ─────────────────────────────────────────────────────────────


class TestWorktreeListTool:
    """Tests for WorktreeListTool."""

    @pytest.fixture
    def tool(self):
        from navig.agent.tools.worktree_tools import WorktreeListTool

        return WorktreeListTool()

    async def test_list_empty(self, tool):
        mock_manager = MagicMock()
        mock_manager.list_worktrees.return_value = []
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({})
        assert result.success is True
        assert "No active" in result.output

    async def test_list_returns_json(self, tool, tmp_path):
        items = [
            {
                "name": "w1",
                "path": str(tmp_path),
                "branch": "navig/w1",
                "age": "5s",
                "merged": False,
                "deleted": False,
            },
        ]
        mock_manager = MagicMock()
        mock_manager.list_worktrees.return_value = items
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({})
        assert result.success is True
        data = json.loads(result.output)
        assert data[0]["name"] == "w1"

    async def test_list_error_returns_failure(self, tool):
        mock_manager = MagicMock()
        mock_manager.list_worktrees.side_effect = RuntimeError("boom")
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({})
        assert result.success is False

    def test_tool_name(self, tool):
        assert tool.name == "worktree_list"

    def test_tool_has_empty_parameters(self, tool):
        assert tool.parameters == []


# ─────────────────────────────────────────────────────────────
# Agent tools — WorktreeMergeTool
# ─────────────────────────────────────────────────────────────


class TestWorktreeMergeTool:
    """Tests for WorktreeMergeTool."""

    @pytest.fixture
    def tool(self):
        from navig.agent.tools.worktree_tools import WorktreeMergeTool

        return WorktreeMergeTool()

    async def test_merge_success(self, tool):
        mock_manager = AsyncMock()
        mock_manager.merge_back.return_value = True
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({"name": "w1"})
        assert result.success is True
        assert "merged successfully" in result.output

    async def test_merge_conflict(self, tool):
        mock_manager = AsyncMock()
        mock_manager.merge_back.return_value = False
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({"name": "w1"})
        assert result.success is False
        assert "conflict" in result.error.lower()

    async def test_merge_missing_name(self, tool):
        result = await tool.run({})
        assert result.success is False
        assert "name" in result.error

    async def test_merge_value_error(self, tool):
        mock_manager = AsyncMock()
        mock_manager.merge_back.side_effect = ValueError("No worktree: missing")
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({"name": "missing"})
        assert result.success is False
        assert "missing" in result.error

    async def test_merge_passes_target_branch(self, tool):
        mock_manager = AsyncMock()
        mock_manager.merge_back.return_value = True
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            await tool.run({"name": "w", "target_branch": "main"})
        mock_manager.merge_back.assert_awaited_once_with(name="w", target_branch="main")

    def test_tool_name(self, tool):
        assert tool.name == "worktree_merge"


# ─────────────────────────────────────────────────────────────
# Agent tools — WorktreeRemoveTool
# ─────────────────────────────────────────────────────────────


class TestWorktreeRemoveTool:
    """Tests for WorktreeRemoveTool."""

    @pytest.fixture
    def tool(self):
        from navig.agent.tools.worktree_tools import WorktreeRemoveTool

        return WorktreeRemoveTool()

    async def test_remove_success(self, tool):
        mock_manager = AsyncMock()
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({"name": "w1"})
        assert result.success is True
        assert "removed" in result.output.lower()

    async def test_remove_missing_name(self, tool):
        result = await tool.run({})
        assert result.success is False
        assert "name" in result.error

    async def test_remove_error_returns_failure(self, tool):
        mock_manager = AsyncMock()
        mock_manager.remove.side_effect = OSError("permission denied")
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            result = await tool.run({"name": "w"})
        assert result.success is False

    async def test_remove_force_true(self, tool):
        mock_manager = AsyncMock()
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            await tool.run({"name": "w", "force": True})
        mock_manager.remove.assert_awaited_once_with(name="w", force=True)

    async def test_remove_force_default_false(self, tool):
        mock_manager = AsyncMock()
        with patch("navig.agent.tools.worktree_tools._get_manager", return_value=mock_manager):
            await tool.run({"name": "w"})
        mock_manager.remove.assert_awaited_once_with(name="w", force=False)

    def test_tool_name(self, tool):
        assert tool.name == "worktree_remove"


# ─────────────────────────────────────────────────────────────
# Tool registration
# ─────────────────────────────────────────────────────────────


class TestToolRegistration:
    """Tests for worktree tool registration in the agent registry."""

    def test_register_worktree_tools_registers_four_tools(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry

        reg = AgentToolRegistry()
        from navig.agent.tools.worktree_tools import (
            WorktreeCreateTool,
            WorktreeListTool,
            WorktreeMergeTool,
            WorktreeRemoveTool,
        )

        reg.register(WorktreeCreateTool(), toolset="worktree")
        reg.register(WorktreeListTool(), toolset="worktree")
        reg.register(WorktreeMergeTool(), toolset="worktree")
        reg.register(WorktreeRemoveTool(), toolset="worktree")
        names = reg.available_names()
        assert "worktree_create" in names
        assert "worktree_list" in names
        assert "worktree_merge" in names
        assert "worktree_remove" in names

    def test_register_worktree_tools_function(self):
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_worktree_tools

        # Save existing names
        before = set(_AGENT_REGISTRY.available_names())
        register_worktree_tools()
        after = set(_AGENT_REGISTRY.available_names())
        new_tools = after - before
        assert "worktree_create" in new_tools
        assert "worktree_list" in new_tools
        assert "worktree_merge" in new_tools
        assert "worktree_remove" in new_tools

    def test_tools_have_get_entry(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry
        from navig.agent.tools.worktree_tools import WorktreeCreateTool

        reg = AgentToolRegistry()
        reg.register(WorktreeCreateTool(), toolset="worktree")
        entry = reg.get_entry("worktree_create")
        assert entry is not None
        assert entry.tool_ref.name == "worktree_create"

    def test_tools_have_openai_schemas(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry
        from navig.agent.tools.worktree_tools import (
            WorktreeCreateTool,
            WorktreeListTool,
        )

        reg = AgentToolRegistry()
        reg.register(WorktreeCreateTool(), toolset="worktree")
        reg.register(WorktreeListTool(), toolset="worktree")
        schemas = reg.get_openai_schemas()
        tool_names = [s["function"]["name"] for s in schemas]
        assert "worktree_create" in tool_names
        assert "worktree_list" in tool_names


# ─────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge-case and defensive tests."""

    async def test_create_name_with_only_hyphens_invalid(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="Invalid worktree name"):
            await mgr.create("---")

    async def test_merge_abort_called_on_conflict(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        abort_called = []

        async def fake_git(cmd: str):
            if "merge --abort" in cmd:
                abort_called.append(True)
            if "merge navig/" in cmd:
                return _completed(1, stderr="CONFLICT")
            return _completed(0, "abc")

        with patch.object(mgr, "_run_git", side_effect=fake_git):
            await mgr.merge_back("w", target_branch="main")
        assert abort_called

    async def test_branch_deleted_on_remove(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        branch_delete_called = []

        async def fake_git(cmd: str):
            if "branch -D" in cmd:
                branch_delete_called.append(cmd)
            return _completed(0)

        with patch.object(mgr, "_run_git", side_effect=fake_git):
            with patch("os.name", "posix"):
                await mgr.remove("w")
        assert branch_delete_called
        assert "navig/w" in branch_delete_called[0]

    async def test_cleanup_all_calls_remove_for_each(self, tmp_path):
        mgr = _make_manager(tmp_path)
        for i in range(5):
            mgr._worktrees[f"w{i}"] = Worktree(
                name=f"w{i}", path=tmp_path / f"w{i}", branch=f"navig/w{i}"
            )
        removed = []
        original_remove = mgr.remove

        async def tracking_remove(name, force=False):
            removed.append(name)
            mgr._worktrees.pop(name, None)

        with patch.object(mgr, "remove", side_effect=tracking_remove):
            with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
                mock_git.return_value = _completed(0)
                count = await mgr.cleanup_all()
        assert count == 5
        assert len(removed) == 5

    def test_list_worktrees_returns_dicts(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="a", path=tmp_path / "a", branch="navig/a")
        mgr._worktrees["a"] = wt
        items = mgr.list_worktrees()
        assert isinstance(items, list)
        assert isinstance(items[0], dict)

    async def test_create_worktree_path_under_worktree_base(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.side_effect = [_completed(0, "true"), _completed(0)]
            wt = await mgr.create("my-worker")
        assert mgr.worktree_base in wt.path.parents or wt.path.parent == mgr.worktree_base

    async def test_merge_cannot_determine_branch_raises(self, tmp_path):
        mgr = _make_manager(tmp_path)
        wt = Worktree(name="w", path=tmp_path / "w", branch="navig/w")
        mgr._worktrees["w"] = wt
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            # rev-parse fails
            mock_git.return_value = _completed(128, stdout="", stderr="error")
            with pytest.raises(RuntimeError, match="Cannot determine current branch"):
                await mgr.merge_back("w")

    async def test_singleton_manager_returned_on_second_call(self, tmp_path):
        m1 = get_manager(repo_root=tmp_path)
        m2 = get_manager()
        assert m1 is m2

    def test_gitignore_entry_constant(self):
        """WORKTREE_DIR constant matches .gitignore plan."""
        assert WORKTREE_DIR == ".navig_worktrees"
