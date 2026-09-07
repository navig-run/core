"""Tests for ``navig repo`` target resolution — the ``--repo`` flag + env hints.

``navig repo stale|lock|conflicts`` used to resolve the repo from the process
cwd alone, with no override, and got "Not inside a git repository" for a
perfectly good repo.

⚠ The dominant cause is navig's OWN chdir, not the shell. ``main.py`` chdir's to
the ACTIVE SPACE during startup, so by the time a command runs the process cwd is
the space. With the default space (``~/.navig-os/workspaces/my-workspace``, not a
git repo) EVERY ``navig repo`` command failed while the operator stood in a repo —
and the error even named the space as what it tried. With a space that IS a repo
it is worse: the command silently operates on the space's repo. ``main.py``
records the pre-chdir directory in ``NAVIG_INVOCATION_CWD``, which now takes
precedence over the cwd.

A launched ``navig.exe`` also does not always inherit the shell's directory (on
Windows, PowerShell's Set-Location moves the *shell* location but not
``[Environment]::CurrentDirectory``), which is why the env hints remain.

These cover the resolver precedence and that the CLI honours ``--repo``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.repo import repo_app, repo_root, resolve_repo_root

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


@pytest.fixture(autouse=True)
def _no_ambient_invocation_hint(monkeypatch):
    """A real `navig` run exports NAVIG_INVOCATION_CWD; these cases must not inherit it.

    Without this every cwd-precedence case below silently exercises the hint branch
    instead — green for the wrong reason.
    """
    monkeypatch.delenv("NAVIG_INVOCATION_CWD", raising=False)


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


def test_invocation_cwd_used_when_navig_has_chdired_to_a_non_repo_space(
    repo: Path, outside: Path, monkeypatch
) -> None:
    """The operator's case: cwd is the active space, which is not a repo at all."""
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(repo))
    root = resolve_repo_root()
    assert root is not None and root.samefile(repo), (
        "resolved from the chdir'd space instead of where the CLI was invoked"
    )


def test_invocation_cwd_beats_a_space_that_is_itself_a_repo(
    repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """The worse case: the space IS a repo, so the command silently targets it.

    This is exactly the "operator standing in repo B" hazard the precedence exists
    to prevent — the chdir turned the protection into its opposite.
    """
    space_repo = _init_repo(tmp_path / "space-repo")
    monkeypatch.chdir(space_repo)
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(repo))
    root = resolve_repo_root()
    assert root is not None and root.samefile(repo)


def test_explicit_repo_still_beats_the_invocation_hint(
    repo: Path, tmp_path: Path, monkeypatch
) -> None:
    other = _init_repo(tmp_path / "repo-explicit")
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(repo))
    root = resolve_repo_root(str(other))
    assert root is not None and root.samefile(other)


def test_a_hint_that_is_not_a_repo_falls_through_to_cwd(
    repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """A stale or non-repo hint must not block a perfectly good cwd.

    ⚠ Needs the same GIT_CEILING_DIRECTORIES as the `outside` fixture: tmp_path sits
    under the real checkout's `.dev/` tree, so without a ceiling this "junk" dir
    resolves UP into the navig repo and is not a non-repo at all.
    """
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    junk = tmp_path / "not-a-repo"
    junk.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(junk))
    monkeypatch.delenv("NAVIG_REPO", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = resolve_repo_root()
    assert root is not None and root.samefile(repo)


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


# ── the gap #1276 left: a RELATIVE --repo (2026-09-06) ──────────────────────
#
# #1276 fixed the BARE case above — the invocation dir now beats the space. The
# explicit branch was left as `repo_root(Path(repo))`, which hands a relative
# path to git with the process cwd still pointing at the space, so
# `navig repo lock --repo .` kept answering "Not inside a git repository.
# tried ." to an operator standing in the repo — the same trap, one line up,
# and the one an operator hits first because the error tells them to pass --repo.


def test_relative_repo_resolves_against_the_invocation_dir(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    space = tmp_path / "active-space"
    space.mkdir()
    monkeypatch.chdir(space)  # where main.py's chdir leaves us
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(repo))

    assert resolve_repo_root(".") == repo_root(repo)


def test_relative_repo_walks_up_from_the_invocation_dir(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = repo / "nested" / "deep"
    sub.mkdir(parents=True)
    space = tmp_path / "active-space"
    space.mkdir()
    monkeypatch.chdir(space)
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(sub))

    assert resolve_repo_root("..") == repo_root(repo)


def test_absolute_repo_ignores_the_invocation_dir(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute --repo is unambiguous and must not be re-based on anything."""
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path / "somewhere-else"))
    assert resolve_repo_root(str(repo)) == repo_root(repo)


def test_cli_honours_a_relative_repo(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: the flag an operator actually types."""
    space = tmp_path / "active-space"
    space.mkdir()
    monkeypatch.chdir(space)
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(repo))

    result = runner.invoke(repo_app, ["lock", "--repo", "."])
    assert result.exit_code == 0, result.output
    assert "not inside a git repository" not in result.output.lower()


def test_hint_shows_the_expansion_instead_of_echoing_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from navig.commands.repo import _resolution_hint

    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path))
    hint = _resolution_hint(".")

    assert str(tmp_path) in hint, "must show what '.' expanded to"
    assert "pass --repo <path>," not in hint, "must not advise the flag just used"
