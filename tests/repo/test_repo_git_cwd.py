"""Regression: ``navig repo`` must not crash when a git cwd is not a real dir.

``_git`` runs every git subprocess with an explicit ``cwd``. ``subprocess``
cannot start a child in a missing/invalid cwd — on Windows ``CreateProcess``
raises ``NotADirectoryError`` (WinError 267), and it does so BEFORE the command
runs, so the ``TimeoutExpired`` guard never catches it. Two real crashes came
from this (schema repo, 2026-08-21):

* ``navig repo remove <slug> --repo E:projectsappsschema`` — a shell stripped
  the backslashes off ``E:\\projects\\apps\\schema``, leaving an invalid
  drive-relative path handed straight to ``git rev-parse`` as cwd.
* ``navig repo prune --yes --repo E:projectsappsschema`` — same path, same crash.

The same defect fires whenever a worktree's folder is gone while a command
probes it (``stale`` on a dangling worktree, ``prune`` re-checking an orphan
that vanished). ``_git`` now degrades an invalid cwd to a failed result, so the
CLI surfaces a clean error / graceful skip instead of a traceback.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.repo import (
    _git,
    _orphan_live_worktree,
    dirty_ref,
    repo_app,
    repo_root,
    resolve_repo_root,
)

runner = CliRunner()


def _git_init(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git_init("init", "-b", "main", cwd=root)
    _git_init("config", "user.email", "test@navig.local", cwd=root)
    _git_init("config", "user.name", "navig-test", cwd=root)
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _git_init("add", "f.txt", cwd=root)
    _git_init("commit", "-m", "base", cwd=root)
    return root


# ── unit: _git degrades, never raises, on an invalid cwd ─────────────────────


def test_git_returns_failed_result_for_missing_cwd(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    res = _git(["rev-parse", "--show-toplevel"], missing)  # must not raise WinError 267
    assert res.returncode != 0
    assert res.stdout == ""


def test_git_returns_failed_result_when_cwd_is_a_file(tmp_path: Path) -> None:
    f = tmp_path / "a-file"
    f.write_text("not a dir\n", encoding="utf-8")
    res = _git(["status", "--porcelain"], f)  # a file is not a valid cwd
    assert res.returncode != 0


def test_git_still_works_for_a_real_repo(repo: Path) -> None:
    res = _git(["rev-parse", "--show-toplevel"], repo)
    assert res.returncode == 0
    assert Path(res.stdout.strip()).resolve() == repo.resolve()


# ── resolver: a bad --repo path is a clean None, never a process-cwd hijack ───


def test_repo_root_none_for_missing_path(tmp_path: Path) -> None:
    assert repo_root(tmp_path / "nope") is None


def test_resolve_repo_root_does_not_fall_back_to_cwd_for_bad_repo(
    repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """An explicit but invalid --repo must resolve to None even when the process
    cwd IS a valid repo — otherwise a mangled path silently operates on the
    wrong repo (the exact hazard behind the WinError-267 crash's argv)."""
    monkeypatch.chdir(repo)
    assert resolve_repo_root(str(tmp_path / "not-a-real-path")) is None


# ── CLI: the two crashing invocations now exit cleanly, not with a traceback ──


def test_repo_remove_bad_repo_path_exits_clean(tmp_path: Path) -> None:
    bad = str(tmp_path / "missing-repo")
    result = runner.invoke(repo_app, ["remove", "ping-v10", "--repo", bad])
    assert result.exit_code == 1  # clean typer.Exit, not an uncaught NotADirectoryError
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "not inside a git repository" in result.output.lower()


def test_repo_prune_bad_repo_path_exits_clean(tmp_path: Path) -> None:
    bad = str(tmp_path / "missing-repo")
    result = runner.invoke(repo_app, ["prune", "--yes", "--repo", bad])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "not inside a git repository" in result.output.lower()


# ── worktree-gone: probing helpers degrade gracefully, never raise ────────────


def test_dirty_ref_on_missing_worktree_is_not_dirty(tmp_path: Path) -> None:
    sha, is_dirty = dirty_ref(tmp_path / "vanished-worktree")  # must not raise
    assert (sha, is_dirty) == (None, False)


def test_orphan_live_probe_on_missing_dir_is_none(tmp_path: Path) -> None:
    assert _orphan_live_worktree(tmp_path / "vanished-orphan") is None  # -> dead leftover, deletable
