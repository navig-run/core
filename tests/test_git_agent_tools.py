"""
Tests for navig.agent.tools.git_tools — Git agent tools.

All tests mock ``_run_git`` and ``_find_git_root`` to avoid touching the
real git repository.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from navig.agent.tools.git_tools import (
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStashTool,
    GitStatusTool,
)

# ── Helpers ──────────────────────────────────────────────────

FAKE_ROOT = Path("/fake/repo")


def _mock_root(start=None):
    """Always return a fake git root."""
    return FAKE_ROOT


def _mock_root_none(start=None):
    """Simulate being outside a git repo."""
    return None


def _git_ok(output: str):
    """Return a mock _run_git that succeeds with *output*."""

    def _mock(args, cwd):
        return True, output

    return _mock


def _git_fail(error: str):
    """Return a mock _run_git that fails."""

    def _mock(args, cwd):
        return False, error

    return _mock


# ── GitStatusTool ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_status_short():
    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _git_ok("## main\n M foo.py")),
    ):
        tool = GitStatusTool()
        result = await tool.run({"short": True})
        assert result.success
        assert "main" in result.output
        assert "foo.py" in result.output


@pytest.mark.asyncio
async def test_git_status_not_a_repo():
    with patch("navig.agent.tools.git_tools._find_git_root", _mock_root_none):
        tool = GitStatusTool()
        result = await tool.run({})
        assert not result.success
        assert "not inside" in result.error.lower()


@pytest.mark.asyncio
async def test_git_status_clean():
    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _git_ok("")),
    ):
        tool = GitStatusTool()
        result = await tool.run({"short": True})
        assert result.success
        assert "clean" in result.output.lower()


# ── GitDiffTool ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_diff_unstaged():
    diff_output = "diff --git a/foo.py b/foo.py\n+new line"
    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _git_ok(diff_output)),
    ):
        tool = GitDiffTool()
        result = await tool.run({})
        assert result.success
        assert "new line" in result.output


@pytest.mark.asyncio
async def test_git_diff_staged():
    """Verify that staged=True passes --cached."""
    calls = []

    def _capture(args, cwd):
        calls.append(args)
        return True, "staged diff output"

    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _capture),
    ):
        tool = GitDiffTool()
        result = await tool.run({"staged": True})
        assert result.success
        assert "--cached" in calls[0]


@pytest.mark.asyncio
async def test_git_diff_with_path():
    calls = []

    def _capture(args, cwd):
        calls.append(args)
        return True, "path diff"

    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _capture),
    ):
        tool = GitDiffTool()
        result = await tool.run({"path": "src/main.py"})
        assert result.success
        assert "--" in calls[0]
        assert "src/main.py" in calls[0]


@pytest.mark.asyncio
async def test_git_diff_empty():
    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _git_ok("")),
    ):
        tool = GitDiffTool()
        result = await tool.run({})
        assert result.success
        assert "no diff" in result.output.lower()


# ── GitLogTool ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_log_default():
    log = "abc1234 Initial commit\ndef5678 Second commit"
    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _git_ok(log)),
    ):
        tool = GitLogTool()
        result = await tool.run({})
        assert result.success
        assert "Initial commit" in result.output


@pytest.mark.asyncio
async def test_git_log_caps_n_at_100():
    """n > 100 should be capped to 100."""
    calls = []

    def _capture(args, cwd):
        calls.append(args)
        return True, "commit"

    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _capture),
    ):
        tool = GitLogTool()
        await tool.run({"n": 500})
        assert "-100" in calls[0]


@pytest.mark.asyncio
async def test_git_log_verbose_format():
    calls = []

    def _capture(args, cwd):
        calls.append(args)
        return True, "verbose output"

    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _capture),
    ):
        tool = GitLogTool()
        await tool.run({"oneline": False})
        assert "--oneline" not in calls[0]
        assert any("--format=" in a for a in calls[0])


# ── GitCommitTool ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_commit_basic():
    calls = []

    def _capture(args, cwd):
        calls.append(list(args))
        if args[0] == "commit":
            return True, "[main abc1234] test commit\n 1 file changed"
        return True, ""

    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _capture),
    ):
        tool = GitCommitTool()
        assert tool.owner_only is True
        result = await tool.run({"message": "test commit"})
        assert result.success
        # Should only have commit call (no add)
        assert calls[0][0] == "commit"


@pytest.mark.asyncio
async def test_git_commit_add_all():
    calls = []

    def _capture(args, cwd):
        calls.append(list(args))
        return True, "[main abc] commit"

    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _capture),
    ):
        tool = GitCommitTool()
        result = await tool.run({"message": "add all", "add_all": True})
        assert result.success
        assert calls[0] == ["add", "-A"]
        assert calls[1][0] == "commit"


@pytest.mark.asyncio
async def test_git_commit_specific_files():
    calls = []

    def _capture(args, cwd):
        calls.append(list(args))
        return True, "[main abc] commit"

    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _capture),
    ):
        tool = GitCommitTool()
        result = await tool.run({"message": "staged", "files": ["a.py", "b.py"]})
        assert result.success
        assert calls[0] == ["add", "--", "a.py", "b.py"]


@pytest.mark.asyncio
async def test_git_commit_no_message():
    with patch("navig.agent.tools.git_tools._find_git_root", _mock_root):
        tool = GitCommitTool()
        result = await tool.run({"message": ""})
        assert not result.success
        assert "required" in result.error.lower()


@pytest.mark.asyncio
async def test_git_commit_nothing_to_commit():
    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch(
            "navig.agent.tools.git_tools._run_git",
            _git_fail("nothing to commit, working tree clean"),
        ),
    ):
        tool = GitCommitTool()
        result = await tool.run({"message": "test"})
        # Treated as success with informational message
        assert result.success
        assert "nothing to commit" in result.output.lower()


# ── GitStashTool ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_stash_list():
    stash_output = "stash@{0}: WIP on main: abc1234 some commit"
    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _git_ok(stash_output)),
    ):
        tool = GitStashTool()
        result = await tool.run({"action": "list"})
        assert result.success
        assert "stash@{0}" in result.output


@pytest.mark.asyncio
async def test_git_stash_push_with_message():
    calls = []

    def _capture(args, cwd):
        calls.append(list(args))
        return True, "Saved working directory"

    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _capture),
    ):
        tool = GitStashTool()
        result = await tool.run({"action": "push", "message": "wip"})
        assert result.success
        assert calls[0] == ["stash", "push", "-m", "wip"]


@pytest.mark.asyncio
async def test_git_stash_pop():
    calls = []

    def _capture(args, cwd):
        calls.append(list(args))
        return True, "On branch main"

    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _capture),
    ):
        tool = GitStashTool()
        result = await tool.run({"action": "pop"})
        assert result.success
        assert calls[0] == ["stash", "pop"]


@pytest.mark.asyncio
async def test_git_stash_invalid_action():
    with patch("navig.agent.tools.git_tools._find_git_root", _mock_root):
        tool = GitStashTool()
        result = await tool.run({"action": "drop"})
        assert not result.success
        assert "unknown" in result.error.lower()


@pytest.mark.asyncio
async def test_git_stash_default_is_list():
    calls = []

    def _capture(args, cwd):
        calls.append(list(args))
        return True, ""

    with (
        patch("navig.agent.tools.git_tools._find_git_root", _mock_root),
        patch("navig.agent.tools.git_tools._run_git", _capture),
    ):
        tool = GitStashTool()
        await tool.run({})
        assert calls[0] == ["stash", "list"]
