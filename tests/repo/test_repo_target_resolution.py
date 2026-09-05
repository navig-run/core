"""Tests for ``navig repo`` target resolution — the ``--repo`` flag + env hints.

``navig repo stale|lock|conflicts`` used to resolve the repo from the process
cwd alone, with no override. A launched ``navig.exe`` does not always inherit
the shell's directory (on Windows, PowerShell's Set-Location / Push-Location
moves the *shell* location but not ``[Environment]::CurrentDirectory``; navig
may also be spawned from a daemon/workspace dir), so an agent driving these
from a subshell got "Not inside a git repository" for a perfectly good repo.

These cover the resolver precedence and that the CLI honours ``--repo``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.repo import repo_app, resolve_repo_root

runner = CliRunner()


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _init_repo(root: Path) -> Path:
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "test@navig.local", cwd=root)
    _git("config", "user.name", "navig-test", cwd=root)
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _git("add", "f.txt", cwd=root)
    _git("commit", "-m", "base", cwd=root)
    return root


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path / "repo")


@pytest.fixture()
def outside(tmp_path: Path, monkeypatch) -> Path:
    """A cwd that is NOT inside any git repo, with env hints cleared.

    pytest's tmp_path lives under the repo's own ``.dev/`` tree, so without a
    ceiling ``git rev-parse`` would climb out of tmp_path and resolve to the
    real navig checkout — masking the not-in-a-repo case. GIT_CEILING_DIRECTORIES
    stops that upward walk at tmp_path (a dir that has no ``.git`` of its own).
    """
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    d = tmp_path / "outside"
    d.mkdir()
    monkeypatch.chdir(d)
    monkeypatch.delenv("NAVIG_REPO", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return d


# ── resolver precedence ──────────────────────────────────────────────────────


def test_explicit_repo_wins(repo: Path, outside: Path) -> None:
    root = resolve_repo_root(str(repo))
    assert root is not None and root.samefile(repo)


def test_cwd_used_when_inside_a_repo(repo: Path, monkeypatch) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("NAVIG_REPO", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = resolve_repo_root()
    assert root is not None and root.samefile(repo)


def test_navig_repo_env_fallback_when_cwd_is_not_a_repo(
    repo: Path, outside: Path, monkeypatch
) -> None:
    monkeypatch.setenv("NAVIG_REPO", str(repo))
    root = resolve_repo_root()
    assert root is not None and root.samefile(repo)


def test_claude_project_dir_fallback(repo: Path, outside: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    root = resolve_repo_root()
    assert root is not None and root.samefile(repo)


def test_navig_repo_beats_claude_project_dir(
    repo: Path, tmp_path: Path, outside: Path, monkeypatch
) -> None:
    other = _init_repo(tmp_path / "other")
    monkeypatch.setenv("NAVIG_REPO", str(repo))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))
    root = resolve_repo_root()
    assert root is not None and root.samefile(repo)


def test_cwd_beats_env_hint(repo: Path, tmp_path: Path, monkeypatch) -> None:
    """Standing inside repo B must not be overridden by an env hint at repo A."""
    other = _init_repo(tmp_path / "repo-b")
    monkeypatch.chdir(other)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    root = resolve_repo_root()
    assert root is not None and root.samefile(other)


def test_none_when_nothing_resolves(outside: Path) -> None:
    assert resolve_repo_root() is None


# ── CLI honours --repo (the whole point) ─────────────────────────────────────


def test_stale_cli_honours_repo_from_outside(repo: Path, outside: Path) -> None:
    result = runner.invoke(repo_app, ["stale", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["default_branch"] == "main"


def test_lock_cli_honours_repo_from_outside(repo: Path, outside: Path) -> None:
    result = runner.invoke(repo_app, ["lock", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    assert "free" in result.output.lower()  # fresh repo holds no lock


def test_conflicts_cli_honours_repo_from_outside(repo: Path, outside: Path) -> None:
    result = runner.invoke(repo_app, ["conflicts", "--repo", str(repo)])
    assert result.exit_code == 0, result.output  # single worktree -> nothing to cross-check


def test_stale_cli_errors_without_repo_outside_a_git_tree(outside: Path) -> None:
    result = runner.invoke(repo_app, ["stale"])
    assert result.exit_code == 1
    assert "not inside a git repository" in result.output.lower()
