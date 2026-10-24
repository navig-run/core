"""The conflict radar's cost reductions, and the soundness they must not trade away.

Measured 2026-10-20: with 104 worktrees the radar ran one ``git merge-tree`` per pair
(~5,300 subprocesses), took over 9 minutes, and four sessions were running it at once —
nobody could get a verdict. The fix prunes pairs that cannot conflict and runs the rest
through a bounded pool. These tests pin both halves:

* the prune fires (so it is not decorative), and
* the two cases that would make it WRONG stay fully simulated — an unmerged conflict,
  and a branch that reached main by squash-merge (``--is-ancestor`` correctly says no).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from navig.commands import repo as repo_mod
from navig.commands.repo import collect_conflicts


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


def _worktree(root: Path, slug: str, branch: str) -> Path:
    wt = root / ".dev" / "worktrees" / slug
    _git("worktree", "add", str(wt), "-b", branch, cwd=root)
    return wt


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake(root: Path, ref_a: str, ref_b: str) -> dict:
        calls.append((ref_a, ref_b))
        return {"status": "clean", "files": []}

    monkeypatch.setattr(repo_mod, "pair_conflicts", fake)
    return calls


def test_pairs_already_in_default_are_pruned(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two branches merged into main → no merge runs, and the verdict is still listed."""
    for slug, branch in (("one", "feat/one"), ("two", "feat/two")):
        wt = _worktree(repo, slug, branch)
        (wt / "f.txt").write_text(f"{branch}\nline2\nline3\n", encoding="utf-8")
        _git("commit", "-am", branch, cwd=wt)
        _git("merge", "--no-ff", "-m", f"merge {branch}", branch, cwd=repo)
    open_wt = _worktree(repo, "open", "feat/open")  # unmerged: still simulated
    (open_wt / "f.txt").write_text("line1\nline2\nopen\n", encoding="utf-8")
    _git("commit", "-am", "open work", cwd=open_wt)

    calls = _spy(monkeypatch)
    data = collect_conflicts(repo, include_dirty=False)

    assert calls, "the unmerged branch must still be simulated"
    assert data["simulated"] == len(calls)
    assert data["pruned"]["in_default"] >= 3  # primary+two merged vs each other
    # Every pair is still reported — a pruned one is clean, not absent.
    sides = len(data["worktrees"])
    assert len(data["pairs"]) == sides * (sides - 1) // 2
    pruned = [p for p in data["pairs"] if p.get("note")]
    assert pruned and all(p["status"] == "clean" for p in pruned)


def test_an_unmerged_conflict_is_still_reported(repo: Path) -> None:
    """The prune must never touch a real collision (no spy: the real merge-tree runs)."""
    wt1 = _worktree(repo, "wt1", "feat/one")
    (wt1 / "f.txt").write_text("WT1\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "wt1", cwd=wt1)
    wt2 = _worktree(repo, "wt2", "feat/two")
    (wt2 / "f.txt").write_text("WT2\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "wt2", cwd=wt2)

    data = collect_conflicts(repo, include_dirty=False)
    conflicts = [p for p in data["pairs"] if p["status"] == "conflict"]
    assert conflicts, "two unmerged edits to one line must conflict"
    assert any("f.txt" in p["files"] for p in conflicts)
    assert data["simulated"] >= 2


def test_a_squashed_merge_is_not_pruned(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Squash-merges are not ancestors of main — skipping them would hide a conflict."""
    wt1 = _worktree(repo, "wt1", "feat/squashed")
    (wt1 / "f.txt").write_text("SQUASHED\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "squash me", cwd=wt1)
    _git("merge", "--squash", "feat/squashed", cwd=repo)
    _git("commit", "-m", "squashed content", cwd=repo)

    wt2 = _worktree(repo, "wt2", "feat/open")
    (wt2 / "f.txt").write_text("line1\nOTHER\nline3\n", encoding="utf-8")
    _git("commit", "-am", "other", cwd=wt2)

    calls = _spy(monkeypatch)
    collect_conflicts(repo, include_dirty=False)

    assert calls, "a squash-merged side must be simulated, never skipped"
    assert any("feat/squashed" in ref for pair in calls for ref in pair), calls


def test_the_payload_reports_what_it_skipped(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The JSON keeps its shape and gains the counts that make the speedup auditable."""
    wt = _worktree(repo, "wt", "feat/only")
    (wt / "f.txt").write_text("only\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "only", cwd=wt)

    data = collect_conflicts(repo, include_dirty=False)
    assert set(data) >= {"worktrees", "pairs", "simulated", "pruned"}
    assert set(data["pruned"]) == {"same_commit", "in_default"}
    assert data["simulated"] + data["pruned"]["same_commit"] + data["pruned"]["in_default"] == len(
        data["pairs"]
    )
    for pair in data["pairs"]:
        assert {"a", "b", "branch_a", "branch_b", "status", "files"} <= set(pair)
