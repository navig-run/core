"""Tests for ``navig repo new`` — create an isolated worktree for a parallel session.

The sanctioned way to spin up a 2nd/3rd Claude session: its own worktree under
``.dev/worktrees/``, branch based on the latest ``origin/<default>``. Throwaway
git repos under tmp_path only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.repo import _new_base_ref, repo_app

runner = CliRunner()


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "test@navig.local", cwd=root)
    _git("config", "user.name", "navig-test", cwd=root)
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _git("add", "f.txt", cwd=root)
    _git("commit", "-m", "base", cwd=root)
    return root


def test_new_creates_worktree_on_typed_branch(repo: Path) -> None:
    result = runner.invoke(repo_app, ["new", "auth-fix", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["branch"] == "feat/auth-fix"
    assert Path(data["path"]) == repo / ".dev" / "worktrees" / "auth-fix"
    assert Path(data["path"]).is_dir()
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout
    assert "auth-fix" in out and "feat/auth-fix" in out


def test_new_honors_type_option(repo: Path) -> None:
    result = runner.invoke(repo_app, ["new", "hotpatch", "-t", "fix", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["branch"] == "fix/hotpatch"


@pytest.mark.parametrize("bad", ["../evil", "Bad-Slug", "a/b", "under_score", "", ".hidden"])
def test_new_rejects_unsafe_slug(repo: Path, bad: str) -> None:
    result = runner.invoke(repo_app, ["new", bad, "--repo", str(repo)])
    assert result.exit_code != 0
    # nothing created for a rejected slug
    assert not (repo / ".dev" / "worktrees" / bad).exists()


def test_new_rejects_bad_type(repo: Path) -> None:
    result = runner.invoke(repo_app, ["new", "x", "--type", "bogus", "--repo", str(repo)])
    assert result.exit_code == 1


def test_new_rejects_existing_dir(repo: Path) -> None:
    (repo / ".dev" / "worktrees" / "dup").mkdir(parents=True)
    result = runner.invoke(repo_app, ["new", "dup", "--repo", str(repo)])
    assert result.exit_code == 1


def test_new_rejects_existing_branch(repo: Path) -> None:
    _git("branch", "feat/taken", cwd=repo)
    result = runner.invoke(repo_app, ["new", "taken", "--repo", str(repo)])
    assert result.exit_code == 1


def test_base_ref_prefers_origin_then_local_then_none(repo: Path, tmp_path: Path) -> None:
    # no remote -> local default branch
    assert _new_base_ref(repo, "main") == "main"
    # a non-existent default branch -> None (worktree add falls back to HEAD)
    assert _new_base_ref(repo, "nonexistent") is None
    # with an origin -> origin/<default> wins (base on latest, not local)
    bare = tmp_path / "origin.git"
    _git("init", "--bare", str(bare), cwd=tmp_path)
    _git("remote", "add", "origin", str(bare), cwd=repo)
    _git("push", "origin", "main", cwd=repo)
    _git("fetch", "origin", cwd=repo)
    assert _new_base_ref(repo, "main") == "origin/main"
