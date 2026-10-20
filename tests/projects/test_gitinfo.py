"""gitinfo.git_state against real repositories.

The project list calls this once per project, so its cost is the cost of opening
Projects: 35 real projects took 17 s cold with four git processes each. These pin
what it reports, and how many processes it may spend doing so.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from navig.projects import gitinfo

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git is not installed",
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=root, check=True, capture_output=True,
    )


def _repo(root: Path, commits: int = 2) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    for i in range(commits):
        (root / "a.txt").write_text(f"v{i}\n", encoding="utf-8")
        _git(root, "add", "a.txt")
        _git(root, "commit", "-q", "-m", f"commit {i}")
    return root


def test_a_folder_without_its_own_repo_is_not_one(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gitinfo.git_state(plain) == {"repo": False}


def test_a_folder_nested_in_another_repo_does_not_borrow_it(tmp_path: Path) -> None:
    outer = _repo(tmp_path / "outer")
    inner = outer / "inner"
    inner.mkdir()
    assert gitinfo.git_state(inner) == {"repo": False}


def test_a_broken_git_dir_is_not_a_repo(tmp_path: Path) -> None:
    root = tmp_path / "broken"
    (root / ".git").mkdir(parents=True)
    assert gitinfo.git_state(root) == {"repo": False}


def test_a_clean_repo_reports_branch_and_recent_commits(tmp_path: Path) -> None:
    state = gitinfo.git_state(_repo(tmp_path / "r"), commits=5)
    assert state["repo"] is True
    assert state["branch"] == "main"
    assert state["dirty"] == 0 and state["untracked"] == 0
    assert [c["subject"] for c in state["recent"]] == ["commit 1", "commit 0"]
    assert state["last_commit"] == state["recent"][0]["date"]


def test_dirty_counts_tracked_changes_and_optionally_untracked(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    (root / "a.txt").write_text("changed\n", encoding="utf-8")
    (root / "new.txt").write_text("x\n", encoding="utf-8")

    full = gitinfo.git_state(root)
    assert (full["dirty"], full["untracked"]) == (2, 1)

    light = gitinfo.git_state(root, commits=1, untracked=False)
    assert (light["dirty"], light["untracked"]) == (1, 0)
    assert len(light["recent"]) == 1


def test_a_detached_head_reads_as_HEAD(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    _git(root, "checkout", "-q", "--detach", "HEAD~1")
    state = gitinfo.git_state(root)
    assert state["branch"] == "HEAD"
    assert state["recent"][0]["subject"] == "commit 0"


def test_a_repo_with_no_commits_yet(tmp_path: Path) -> None:
    state = gitinfo.git_state(_repo(tmp_path / "r", commits=0))
    assert state["repo"] is True
    assert state["branch"] == "main"
    assert state["recent"] == [] and state["last_commit"] is None


def test_it_spends_at_most_two_git_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path / "r")
    calls: list[list[str]] = []
    real = subprocess.run

    def counting(cmd, *a, **k):
        calls.append(list(cmd))
        return real(cmd, *a, **k)

    monkeypatch.setattr(gitinfo.subprocess, "run", counting)
    gitinfo.git_state(root, commits=1, untracked=False)
    assert len(calls) <= 2, calls


def test_a_timeout_is_unknown_not_no_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a 2 s ceiling on a loaded machine reported real repos as "no repo"."""
    root = _repo(tmp_path / "r")

    def slow(cmd, *a, **k):
        raise subprocess.TimeoutExpired(cmd, k.get("timeout", 0))

    monkeypatch.setattr(gitinfo.subprocess, "run", slow)
    state = gitinfo.git_state(root)
    assert state["repo"] is True and state["unknown"] is True
    assert state["branch"] is None and state["dirty"] is None


def test_the_project_list_does_not_remember_an_unknown_git_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from navig.projects import model

    root = _repo(tmp_path / "r")
    answers = iter([{"repo": True, "unknown": True}, {"repo": True, "branch": "main"}])
    monkeypatch.setattr(model.gitinfo, "git_state", lambda *a, **k: next(answers))
    model.clear_cache()
    try:
        assert model._git_light(root).get("unknown") is True
        assert model._git_light(root)["branch"] == "main"  # retried, not served from cache
    finally:
        model.clear_cache()


def test_head_branch_reads_the_branch_without_git(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    assert gitinfo.head_branch(root) == "main"
    _git(root, "checkout", "-q", "--detach", "HEAD")
    assert gitinfo.head_branch(root) == "HEAD"
    assert gitinfo.head_branch(tmp_path / "nope") is None


def test_head_branch_follows_a_worktree_git_file(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r")
    _git(root, "worktree", "add", "-q", "-b", "feat/x", str(tmp_path / "wt"))
    assert (tmp_path / "wt" / ".git").is_file()
    assert gitinfo.head_branch(tmp_path / "wt") == "feat/x"


def test_the_fast_list_never_waits_on_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a cold Projects list took 10-14 s on a busy machine (OS limit: 15 s)
    because it ran git for every repo before answering."""
    import threading

    from navig.projects import model

    root = _repo(tmp_path / "r")
    release = threading.Event()
    calls: list[str] = []

    def slow_state(*a, **k):
        calls.append("git")
        release.wait(5)
        return {"repo": True, "branch": "main", "dirty": 3, "last_commit": "2026-10-10T00:00:00+00:00"}

    monkeypatch.setattr(model.gitinfo, "git_state", slow_state)
    model.clear_cache()
    try:
        first = model._git_light(root, fast=True)
        assert first == {"repo": True, "branch": "main", "dirty": None, "last_commit": None, "pending": True}
        release.set()
        for _ in range(100):  # the background read lands in the cache
            seen = model._peek("git", root, model._git_sig(root))
            if seen is not None:
                break
            threading.Event().wait(0.05)
        assert model._git_light(root, fast=True)["dirty"] == 3
        assert calls == ["git"]  # read once, in the background, never inline
    finally:
        release.set()
        model.clear_cache()


def test_the_fast_list_says_no_repo_at_once_for_a_folder_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured: 13 code folders with no `.git` of their own came back as pending repos."""
    from navig.projects import model

    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr(model, "_warm_git", lambda root: pytest.fail("queued a git read for a non-repo"))
    assert model._git_light(plain, fast=True) == {"repo": False}
