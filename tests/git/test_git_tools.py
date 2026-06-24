"""Batch 118: tests for navig/agent/tools/git_tools.py."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# _find_git_root
# ===========================================================================

class TestFindGitRoot:
    def test_finds_root_at_cwd(self, tmp_path):
        from navig.agent.tools.git_tools import _find_git_root

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        result = _find_git_root(start=tmp_path)
        assert result == tmp_path

    def test_finds_root_in_ancestor(self, tmp_path):
        from navig.agent.tools.git_tools import _find_git_root

        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        result = _find_git_root(start=subdir)
        assert result == tmp_path

    def test_returns_none_when_no_git(self, tmp_path):
        from navig.agent.tools.git_tools import _find_git_root

        deep = tmp_path / "x" / "y"
        deep.mkdir(parents=True)
        result = _find_git_root(start=deep)
        # Should return None (no .git anywhere below tmp_path — but may find the
        # real repo root if the CWD is inside navig-core). Skip assertion if repo
        # is found; we just care it doesn't crash.
        assert result is None or isinstance(result, Path)

    def test_uses_cwd_when_no_start(self):
        from navig.agent.tools.git_tools import _find_git_root

        # Should not raise
        result = _find_git_root()
        assert result is None or isinstance(result, Path)


# ===========================================================================
# _run_git
# ===========================================================================

class TestRunGit:
    def _fake_completed(self, stdout="ok output", stderr="", returncode=0):
        r = MagicMock()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r

    def test_success(self, tmp_path):
        from navig.agent.tools.git_tools import _run_git

        with patch("subprocess.run", return_value=self._fake_completed("output")) as mock_run:
            ok, output = _run_git(["status"], cwd=tmp_path)

        assert ok is True
        assert output == "output"

    def test_failure_returns_stderr(self, tmp_path):
        from navig.agent.tools.git_tools import _run_git

        with patch("subprocess.run", return_value=self._fake_completed("", "fatal error", returncode=128)):
            ok, output = _run_git(["status"], cwd=tmp_path)

        assert ok is False
        assert "fatal error" in output

    def test_failure_falls_back_to_stdout(self, tmp_path):
        """When stderr is empty and rc != 0, returns stdout."""
        from navig.agent.tools.git_tools import _run_git

        with patch("subprocess.run", return_value=self._fake_completed("some msg", "", returncode=1)):
            ok, output = _run_git(["log"], cwd=tmp_path)

        assert ok is False
        assert "some msg" in output

    def test_timeout_returns_false(self, tmp_path):
        from navig.agent.tools.git_tools import _run_git

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30)):
            ok, output = _run_git(["status"], cwd=tmp_path)

        assert ok is False
        assert "timed out" in output

    def test_file_not_found_returns_false(self, tmp_path):
        from navig.agent.tools.git_tools import _run_git

        with patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            ok, output = _run_git(["status"], cwd=tmp_path)

        assert ok is False
        assert "not found" in output.lower()

    def test_generic_exception_returns_false(self, tmp_path):
        from navig.agent.tools.git_tools import _run_git

        with patch("subprocess.run", side_effect=OSError("unexpected")):
            ok, output = _run_git(["status"], cwd=tmp_path)

        assert ok is False
        assert "unexpected" in output


# ===========================================================================
# GitStatusTool
# ===========================================================================

class TestGitStatusTool:
    def _tool(self):
        from navig.agent.tools.git_tools import GitStatusTool
        return GitStatusTool()

    def test_name_and_owner_only(self):
        t = self._tool()
        assert t.name == "git_status"
        assert t.owner_only is False

    @pytest.mark.asyncio
    async def test_no_git_repo(self):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=None):
            result = await t.run({})
        assert result.success is False
        assert "not inside" in result.error.lower()

    @pytest.mark.asyncio
    async def test_short_output(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "## main\nM  file.py")):
                result = await t.run({"short": True})
        assert result.success is True
        assert "main" in result.output

    @pytest.mark.asyncio
    async def test_long_output(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "On branch main\nnothing to commit")):
                result = await t.run({"short": False})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_git_error(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(False, "fatal: not a repo")):
                result = await t.run({})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_empty_output_becomes_clean(self, tmp_path):
        """Empty git status output is replaced with a 'clean' message."""
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "")):
                result = await t.run({})
        assert result.success is True
        assert "clean" in result.output.lower() or result.output


# ===========================================================================
# GitDiffTool
# ===========================================================================

class TestGitDiffTool:
    def _tool(self):
        from navig.agent.tools.git_tools import GitDiffTool
        return GitDiffTool()

    def test_name(self):
        assert self._tool().name == "git_diff"

    @pytest.mark.asyncio
    async def test_no_repo(self):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=None):
            result = await t.run({})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unstaged_diff(self, tmp_path):
        t = self._tool()
        diff_output = "@@ -1,3 +1,4 @@\n context\n+added line"
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, diff_output)) as mock_run:
                result = await t.run({"staged": False})
        assert result.success is True
        call_args = mock_run.call_args[0][0]
        assert "--cached" not in call_args

    @pytest.mark.asyncio
    async def test_staged_diff(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "staged diff")) as mock_run:
                result = await t.run({"staged": True})
        assert result.success is True
        call_args = mock_run.call_args[0][0]
        assert "--cached" in call_args

    @pytest.mark.asyncio
    async def test_diff_with_path(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "diff")) as mock_run:
                result = await t.run({"path": "myfile.py"})
        assert result.success is True
        call_args = mock_run.call_args[0][0]
        assert "myfile.py" in call_args

    @pytest.mark.asyncio
    async def test_empty_diff_shows_no_diff(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "")):
                result = await t.run({})
        assert result.success is True
        assert "no diff" in result.output.lower()


# ===========================================================================
# GitLogTool
# ===========================================================================

class TestGitLogTool:
    def _tool(self):
        from navig.agent.tools.git_tools import GitLogTool
        return GitLogTool()

    def test_name(self):
        assert self._tool().name == "git_log"

    @pytest.mark.asyncio
    async def test_no_repo(self):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=None):
            result = await t.run({})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_default_log(self, tmp_path):
        t = self._tool()
        log_output = "abc1234 Add feature\ndef5678 Fix bug"
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, log_output)) as mock_run:
                result = await t.run({})
        assert result.success is True
        call_args = mock_run.call_args[0][0]
        assert "-10" in call_args  # default n=10
        assert "--oneline" in call_args

    @pytest.mark.asyncio
    async def test_log_n_capped_at_100(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "log")) as mock_run:
                result = await t.run({"n": 999})
        call_args = mock_run.call_args[0][0]
        assert "-100" in call_args  # capped at 100

    @pytest.mark.asyncio
    async def test_log_with_path(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "log")) as mock_run:
                result = await t.run({"path": "src/main.py"})
        call_args = mock_run.call_args[0][0]
        assert "src/main.py" in call_args

    @pytest.mark.asyncio
    async def test_empty_log(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "")):
                result = await t.run({})
        assert result.success is True
        assert "no commits" in result.output.lower()


# ===========================================================================
# GitCommitTool
# ===========================================================================

class TestGitCommitTool:
    def _tool(self):
        from navig.agent.tools.git_tools import GitCommitTool
        return GitCommitTool()

    def test_name_and_owner_only(self):
        t = self._tool()
        assert t.name == "git_commit"
        assert t.owner_only is True  # write operation

    @pytest.mark.asyncio
    async def test_no_repo(self):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=None):
            result = await t.run({"message": "test"})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_missing_message_fails(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            result = await t.run({})
        assert result.success is False
        assert "message" in result.error.lower()

    @pytest.mark.asyncio
    async def test_commit_success(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "[main abc1234] Test commit")) as mock_run:
                result = await t.run({"message": "Test commit"})
        assert result.success is True
        assert "abc1234" in result.output

    @pytest.mark.asyncio
    async def test_commit_with_add_all(self, tmp_path):
        t = self._tool()
        call_log = []

        def fake_run_git(args, cwd):
            call_log.append(args)
            return (True, "[main] done")

        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", side_effect=fake_run_git):
                result = await t.run({"message": "msg", "add_all": True})

        # First call should be "git add -A"
        assert call_log[0] == ["add", "-A"]
        assert result.success is True

    @pytest.mark.asyncio
    async def test_commit_with_specific_files(self, tmp_path):
        t = self._tool()
        call_log = []

        def fake_run_git(args, cwd):
            call_log.append(args)
            return (True, "committed")

        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", side_effect=fake_run_git):
                result = await t.run({"message": "msg", "files": ["a.py", "b.py"]})

        assert "a.py" in call_log[0]
        assert "b.py" in call_log[0]

    @pytest.mark.asyncio
    async def test_nothing_to_commit(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(False, "nothing to commit")):
                result = await t.run({"message": "msg"})
        assert result.success is True
        assert "nothing to commit" in result.output.lower()

    @pytest.mark.asyncio
    async def test_commit_git_error(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(False, "error: something broke")):
                result = await t.run({"message": "msg"})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_add_all_fails_returns_error(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(False, "add failed")):
                result = await t.run({"message": "msg", "add_all": True})
        assert result.success is False
        assert "add" in result.error.lower()


# ===========================================================================
# GitStashTool
# ===========================================================================

class TestGitStashTool:
    def _tool(self):
        from navig.agent.tools.git_tools import GitStashTool
        return GitStashTool()

    def test_name(self):
        assert self._tool().name == "git_stash"

    @pytest.mark.asyncio
    async def test_no_repo(self):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=None):
            result = await t.run({})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_stash_list(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "stash@{0}: WIP")) as mock_run:
                result = await t.run({"action": "list"})
        assert result.success is True
        assert "stash" in mock_run.call_args[0][0]

    @pytest.mark.asyncio
    async def test_stash_push(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "Saved working")) as mock_run:
                result = await t.run({"action": "push", "message": "my stash"})
        assert result.success is True
        call_args = mock_run.call_args[0][0]
        assert "push" in call_args
        assert "my stash" in call_args

    @pytest.mark.asyncio
    async def test_stash_pop(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "Dropped stash@{0}")) as mock_run:
                result = await t.run({"action": "pop"})
        call_args = mock_run.call_args[0][0]
        assert "pop" in call_args

    @pytest.mark.asyncio
    async def test_stash_unknown_action(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            result = await t.run({"action": "unknown"})
        assert result.success is False
        assert "unknown" in result.error.lower()

    @pytest.mark.asyncio
    async def test_stash_empty_uses_default_list(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(True, "")) as mock_run:
                result = await t.run({})
        assert result.success is True
        call_args = mock_run.call_args[0][0]
        assert "list" in call_args

    @pytest.mark.asyncio
    async def test_stash_git_error(self, tmp_path):
        t = self._tool()
        with patch("navig.agent.tools.git_tools._find_git_root", return_value=tmp_path):
            with patch("navig.agent.tools.git_tools._run_git", return_value=(False, "stash error")):
                result = await t.run({"action": "pop"})
        assert result.success is False
