"""Unit tests for navig.selfheal.git_manager."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.selfheal.git_manager import (
    UPSTREAM_URL,
    apply_patch,
    create_branch,
    sync_fork,
)


class TestSyncForkCreatesRemote:
    """sync_fork() must add the upstream remote before fetching."""

    def test_sync_fork_adds_upstream_remote(self, tmp_path: Path) -> None:
        """Verify that git remote add upstream is called with UPSTREAM_URL."""
        calls_made: list[list[str]] = []

        def fake_run(
            cmd: list[str],
            **kwargs,
        ) -> MagicMock:
            calls_made.append(cmd)
            result = MagicMock()
            result.stdout = ""
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=fake_run):
            sync_fork(tmp_path)

        # The very first git call must be `git remote add upstream <url>`
        assert calls_made[0] == ["git", "remote", "add", "upstream", UPSTREAM_URL]

    def test_sync_fork_fetches_upstream(self, tmp_path: Path) -> None:
        """After adding remote, sync_fork must fetch from upstream."""
        commands: list[str] = []

        def fake_run(cmd: list[str], **kwargs) -> MagicMock:
            commands.append(cmd[1] if len(cmd) > 1 else "")
            result = MagicMock()
            result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=fake_run):
            sync_fork(tmp_path)

        assert "fetch" in commands

    def test_sync_fork_pushes_with_force_with_lease(self, tmp_path: Path) -> None:
        """Sync must push with --force-with-lease to protect concurrent changes."""
        push_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs) -> MagicMock:
            if "push" in cmd:
                push_calls.append(cmd)
            result = MagicMock()
            result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=fake_run):
            sync_fork(tmp_path)

        assert push_calls, "Expected at least one git push call"
        push_args = push_calls[-1]
        assert "--force-with-lease" in push_args


class TestCreateBranchNaming:
    """create_branch() must produce correctly-formatted branch names."""

    def test_branch_name_starts_with_selfheal_prefix(self, tmp_path: Path) -> None:
        """Branch name must start with 'navig-selfheal/'."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            branch = create_branch(tmp_path)
        assert branch.startswith("navig-selfheal/")

    def test_branch_name_contains_date(self, tmp_path: Path) -> None:
        """Branch name must contain an 8-digit YYYYMMDD date component."""
        import re

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            branch = create_branch(tmp_path)

        # Extract date portion: navig-selfheal/YYYYMMDD-xxxxxxxx
        match = re.match(r"navig-selfheal/(\d{8})-([0-9a-f]{8})", branch)
        assert match is not None, f"Branch '{branch}' does not match expected format"
        date_part = match.group(1)
        assert len(date_part) == 8
        assert date_part.isdigit()

    def test_branch_name_has_unique_hash_suffix(self, tmp_path: Path) -> None:
        """Two consecutive calls must produce different branch names."""
        branches: list[str] = []

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            branches.append(create_branch(tmp_path))
            branches.append(create_branch(tmp_path))

        assert branches[0] != branches[1], "Branch names should be unique across calls"


class TestApplyPatchCallsGit:
    """apply_patch() must call git apply --check then git apply."""

    def test_apply_patch_runs_check_first(self, tmp_path: Path) -> None:
        """git apply --check must precede the actual git apply."""
        (tmp_path / ".git").mkdir()
        patch_content = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        git_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs) -> MagicMock:
            git_calls.append(cmd)
            result = MagicMock()
            result.stdout = ""
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=fake_run):
            apply_patch(tmp_path, patch_content)

        check_call = next(
            (c for c in git_calls if "apply" in c and "--check" in c), None
        )
        assert check_call is not None, "git apply --check was never called"

    def test_apply_patch_calls_git_apply_after_check(self, tmp_path: Path) -> None:
        """The real git apply must follow the --check call."""
        (tmp_path / ".git").mkdir()
        patch_content = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        apply_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs) -> MagicMock:
            if "apply" in cmd and "--check" not in cmd:
                apply_calls.append(cmd)
            result = MagicMock()
            result.stdout = ""
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=fake_run):
            apply_patch(tmp_path, patch_content)

        assert apply_calls, "git apply (without --check) was never called"

    def test_apply_patch_raises_on_check_failure(self, tmp_path: Path) -> None:
        """apply_patch must propagate CalledProcessError from --check."""
        (tmp_path / ".git").mkdir()
        patch_content = "bad patch content"

        def fake_run(cmd: list[str], **kwargs) -> MagicMock:
            if "--check" in cmd:
                raise subprocess.CalledProcessError(
                    1, cmd, stderr=b"patch does not apply"
                )
            return MagicMock(stdout="", returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(subprocess.CalledProcessError):
                apply_patch(tmp_path, patch_content)
