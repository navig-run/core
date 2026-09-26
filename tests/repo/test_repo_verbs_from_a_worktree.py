"""Every `navig repo` verb, run from INSIDE a linked worktree, acts on the MAIN tree.

`repo_root(wt)` has a unit test. The verbs do not — and the resolver was fine the day
`navig repo new b`, run from `.dev/worktrees/a`, created `.dev/worktrees/a/.dev/worktrees/b`
(#1443): the verb reached its root a different way. The same shape hid the hook bug in
#1481, because every hook test ran from the main tree. A verb is proven only when it is
DRIVEN from a worktree cwd and its output is checked against the main tree.

`NAVIG_INVOCATION_CWD` is how the real CLI conveys the pre-chdir directory (`main.py`
chdirs into the active space before a command body runs), so the tests set it exactly as
the launcher would.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands import repo as repo_mod
from navig.commands.repo import repo_app

runner = CliRunner()


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


def _commit(root: Path, name: str) -> None:
    (root / name).write_text("x\n", encoding="utf-8")
    _git("add", name, cwd=root)
    _git("commit", "-q", "-m", f"add {name}", cwd=root)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@navig.local", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    _commit(root, "base.txt")
    _git("remote", "add", "origin", str(bare), cwd=root)
    _git("push", "-q", "-u", "origin", "main", cwd=root)
    return root


@pytest.fixture()
def wt(repo: Path) -> Path:
    w = repo / ".dev" / "worktrees" / "a"
    w.parent.mkdir(parents=True)
    _git("worktree", "add", "-q", str(w), "-b", "feat/a", cwd=repo)
    return w


@pytest.fixture(autouse=True)
def _from_inside_the_worktree(wt: Path, monkeypatch):
    """The invocation context of a session opened in `.dev/worktrees/a`."""
    monkeypatch.chdir(wt)
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(wt))
    monkeypatch.delenv("NAVIG_REPO", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(repo_mod, "_github_pr_index", lambda root: ({}, "gh not installed"))


def _same(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve()


def test_stale_reports_the_main_tree_and_lists_this_worktree(repo: Path, wt: Path) -> None:
    result = runner.invoke(repo_app, ["stale", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    paths = [Path(w["path"]) for w in data["worktrees"]]
    assert any(_same(p, wt) for p in paths), "the worktree we are IN must appear as an extra worktree of the main tree"
    assert not any(_same(p, repo) for p in paths), "the main tree must not be listed as an extra worktree of itself"


def test_new_creates_a_sibling_under_the_main_tree_not_a_nested_one(repo: Path, wt: Path) -> None:
    """THE #1443 regression, driven through the verb."""
    result = runner.invoke(repo_app, ["new", "b", "--no-fetch"] if "--no-fetch" in _new_help() else ["new", "b"])
    assert result.exit_code == 0, result.output
    sibling = repo / ".dev" / "worktrees" / "b"
    nested = wt / ".dev" / "worktrees" / "b"
    assert sibling.exists(), f"`new b` from worktree a must create {sibling}\n{result.output}"
    assert not nested.exists(), "…and never a worktree nested inside worktree a"


def test_sweep_judges_against_the_main_trees_base(repo: Path, wt: Path) -> None:
    result = runner.invoke(repo_app, ["sweep", "--no-fetch", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["default_branch"] == "main"
    # our own worktree is a fresh one: listed as finished, never removable
    ours = [w for w in data["finished_worktrees"] if _same(Path(w["path"]), wt)]
    assert ours and ours[0]["removable"] is False


def test_remove_by_slug_finds_the_main_trees_worktree(repo: Path, wt: Path) -> None:
    other = repo / ".dev" / "worktrees" / "c"
    _git("worktree", "add", "-q", str(other), "-b", "feat/c", cwd=repo)
    result = runner.invoke(repo_app, ["remove", "c"])
    assert result.exit_code == 0, result.output
    assert not other.exists()


def test_conflicts_pairs_the_main_checkout_with_this_worktree(repo: Path, wt: Path) -> None:
    result = runner.invoke(repo_app, ["conflicts", "--json"])
    assert result.exit_code in (0, 2), result.output  # 2 = a collision found, still a valid run
    data = json.loads(result.output)
    primary = [w for w in data["worktrees"] if w["primary"]]
    assert len(primary) == 1 and _same(Path(primary[0]["path"]), repo), (
        "the PRIMARY must be the main tree — from inside a worktree the old resolver made the "
        "worktree primary and the main tree vanish: " + json.dumps(data, indent=1)
    )
    assert any(_same(Path(w["path"]), wt) and not w["primary"] for w in data["worktrees"])
    assert any({pr["a"], pr["b"]} & {"a"} and "main checkout" in pr["a"] + pr["b"] for pr in data["pairs"]), (
        "the radar must compare this worktree against the main checkout"
    )


def _new_help() -> str:
    return runner.invoke(repo_app, ["new", "--help"]).output
