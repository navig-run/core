"""Tests for ``navig repo`` — cross-worktree conflict radar + stale-work report.

Builds throwaway git repos under tmp_path; never touches the real checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from navig.commands.repo import (
    collect_conflicts,
    collect_stale,
    list_worktrees,
    lock_state,
    pair_conflicts,
)


def _git(*args: str, cwd: Path) -> str:
    res = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return res.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "test@navig.local", cwd=root)
    _git("config", "user.name", "navig-test", cwd=root)
    (root / "f.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    _git("add", "f.txt", cwd=root)
    _git("commit", "-m", "base", cwd=root)
    return root


def _add_worktree(root: Path, slug: str, branch: str) -> Path:
    wt = root / ".dev" / "worktrees" / slug
    _git("worktree", "add", str(wt), "-b", branch, cwd=root)
    return wt


def test_list_worktrees_flags_primary_and_sibling(repo: Path, tmp_path: Path) -> None:
    _add_worktree(repo, "inside", "feat/inside")
    sibling = tmp_path / "sibling-wt"
    _git("worktree", "add", str(sibling), "-b", "feat/sibling", cwd=repo)

    wts = {wt["path"].name: wt for wt in list_worktrees(repo)}
    assert wts["repo"]["is_primary"] and not wts["repo"]["is_sibling"]
    assert not wts["inside"]["is_primary"] and not wts["inside"]["is_sibling"]
    assert wts["sibling-wt"]["is_sibling"]


def test_conflicting_committed_edits_detected(repo: Path) -> None:
    wt = _add_worktree(repo, "wt1", "feat/one")
    (wt / "f.txt").write_text("WT1\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "wt1 edit", cwd=wt)
    (repo / "f.txt").write_text("MAIN\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "main edit", cwd=repo)

    data = collect_conflicts(repo)
    assert len(data["pairs"]) == 1
    pair = data["pairs"][0]
    assert pair["status"] == "conflict"
    assert "f.txt" in pair["files"]


def test_disjoint_edits_are_clean(repo: Path) -> None:
    wt = _add_worktree(repo, "wt2", "feat/two")
    (wt / "g.txt").write_text("new file\n", encoding="utf-8")
    _git("add", "g.txt", cwd=wt)
    _git("commit", "-m", "wt2 new file", cwd=wt)
    (repo / "f.txt").write_text("MAIN\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "main edit", cwd=repo)

    data = collect_conflicts(repo)
    assert [p["status"] for p in data["pairs"]] == ["clean"]


def test_uncommitted_dirty_state_is_seen(repo: Path) -> None:
    """The clash-beating case: conflict visible while still uncommitted."""
    wt = _add_worktree(repo, "wt3", "feat/three")
    (wt / "f.txt").write_text("WT3-DIRTY\nline2\nline3\n", encoding="utf-8")  # no commit
    (repo / "f.txt").write_text("MAIN\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "main edit", cwd=repo)

    committed_only = collect_conflicts(repo, include_dirty=False)
    assert [p["status"] for p in committed_only["pairs"]] == ["clean"]

    with_dirty = collect_conflicts(repo, include_dirty=True)
    pair = with_dirty["pairs"][0]
    assert pair["status"] == "conflict"
    assert "f.txt" in pair["files"]
    # simulation must never touch the worktree
    assert (wt / "f.txt").read_text(encoding="utf-8").startswith("WT3-DIRTY")


def test_pair_conflicts_no_common_history(repo: Path) -> None:
    _git("checkout", "--orphan", "orphan", cwd=repo)
    (repo / "o.txt").write_text("x\n", encoding="utf-8")
    _git("add", "o.txt", cwd=repo)
    _git("commit", "-m", "orphan", cwd=repo)
    assert pair_conflicts(repo, "main", "orphan")["status"] == "no-base"
    _git("checkout", "main", cwd=repo)


def test_collect_stale_reports_branch_stash_and_worktree(repo: Path) -> None:
    _git("branch", "feat/leftover", cwd=repo)
    _git("checkout", "feat/leftover", cwd=repo)
    (repo / "f.txt").write_text("LEFTOVER\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "leftover work", cwd=repo)
    _git("checkout", "main", cwd=repo)

    (repo / "f.txt").write_text("stash me\nline2\nline3\n", encoding="utf-8")
    _git("stash", cwd=repo)

    _add_worktree(repo, "wt4", "feat/four")

    data = collect_stale(repo)
    names = {b["name"] for b in data["unmerged_branches"]}
    assert "feat/leftover" in names
    leftover = next(b for b in data["unmerged_branches"] if b["name"] == "feat/leftover")
    assert leftover["ahead"] == 1
    assert len(data["stashes"]) == 1
    assert [Path(wt["path"]).name for wt in data["worktrees"]] == ["wt4"]
    assert data["lock"] is None
    assert lock_state(data["lock"])["state"] == "free"
