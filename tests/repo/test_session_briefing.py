"""Tests for the SessionStart briefing hook — the cross-worktree conflict radar.

Loads the hook by file path (stdlib-only script outside the navig package).
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HOOK = _REPO_ROOT / "scripts" / "agent-hooks" / "session_start.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("session_start_hook", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


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


def test_single_worktree_has_no_radar_lines(hook, repo: Path) -> None:
    assert hook.conflict_lines(repo) == []


def test_clean_pair_reports_all_clean(hook, repo: Path) -> None:
    wt = repo / ".dev" / "worktrees" / "wt1"
    _git("worktree", "add", str(wt), "-b", "feat/one", cwd=repo)
    (wt / "g.txt").write_text("new\n", encoding="utf-8")
    _git("add", "g.txt", cwd=wt)
    _git("commit", "-m", "disjoint", cwd=wt)

    lines = hook.conflict_lines(repo)
    assert len(lines) == 1
    assert "1 pair(s), all clean" in lines[0]


def test_conflicting_pair_is_flagged_including_dirty_state(hook, repo: Path) -> None:
    wt = repo / ".dev" / "worktrees" / "wt2"
    _git("worktree", "add", str(wt), "-b", "feat/two", cwd=repo)
    # uncommitted edit in the worktree vs committed edit on main -> collision
    (wt / "f.txt").write_text("WT2-DIRTY\nline2\nline3\n", encoding="utf-8")
    (repo / "f.txt").write_text("MAIN\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "main edit", cwd=repo)

    lines = hook.conflict_lines(repo)
    assert len(lines) == 1
    assert "merge conflict brewing" in lines[0]
    assert "f.txt" in lines[0]
    # the radar must never mutate the worktree
    assert (wt / "f.txt").read_text(encoding="utf-8").startswith("WT2-DIRTY")

    # and the full briefing carries the radar line
    assert "merge conflict brewing" in hook.briefing(repo)
