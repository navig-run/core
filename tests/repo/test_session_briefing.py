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


def test_briefing_counts_orphaned_worktree_dirs(hook, repo: Path) -> None:
    """A physical .dev/worktrees dir git no longer tracks is surfaced as a count.

    Registered worktrees are excluded; only the untracked leftover is counted.
    """
    wt = repo / ".dev" / "worktrees" / "live"
    _git("worktree", "add", str(wt), "-b", "feat/live", cwd=repo)
    (repo / ".dev" / "worktrees" / "dead").mkdir(parents=True)  # orphan leftover

    out = hook.briefing(repo)
    assert "1 orphaned worktree dir(s)" in out
    assert "navig repo prune" in out


def test_briefing_names_a_worktree_whose_add_died(hook, repo: Path) -> None:
    """A worktree still locked "initializing" is a partial checkout from an add that died
    (2026-09-15: a 15 s ceiling killed `git` mid-checkout; the tree looked normal in
    `git worktree list` and refused `repo remove` for hours). The briefing must say so
    on the worktree's own line, with the command that clears it - not list it as a
    normal place to work. A deliberate lock with any other reason is left alone."""
    dead = repo / ".dev" / "worktrees" / "dead-add"
    _git("worktree", "add", str(dead), "-b", "feat/dead-add", cwd=repo)
    _git("worktree", "lock", "--reason", "initializing", str(dead), cwd=repo)
    held = repo / ".dev" / "worktrees" / "held"
    _git("worktree", "add", str(held), "-b", "feat/held", cwd=repo)
    _git("worktree", "lock", "--reason", "reviewing", str(held), cwd=repo)

    out = hook.briefing(repo)
    dead_line = next(ln for ln in out.splitlines() if "dead-add" in ln)
    assert "DIED" in dead_line and "locked: initializing" in dead_line
    assert "navig repo remove dead-add --force" in dead_line
    held_line = next(ln for ln in out.splitlines() if "worktrees" in ln and "held" in ln)
    assert "DIED" not in held_line
def test_briefing_counts_changelog_fragments_awaiting_assembly(hook, repo: Path) -> None:
    """core/changelog.d/ fragments pile up until a release; the briefing keeps the pile
    visible with the command that folds it. The README is not a fragment. A repo without
    the directory says nothing."""
    assert "changelog fragment" not in hook.briefing(repo)
    d = repo / "core" / "changelog.d"
    d.mkdir(parents=True)
    (d / "README.md").write_text("# contract\n", encoding="utf-8")
    assert "changelog fragment" not in hook.briefing(repo)
    (d / "one.fixed.md").write_text("- **One.**\n", encoding="utf-8")
    (d / "two.added.md").write_text("- **Two.**\n", encoding="utf-8")
    out = hook.briefing(repo)
    assert "2 changelog fragment(s) awaiting assembly" in out
    assert "npm run changelog:assemble" in out


# ── the lock line says what to DO, not just that a lock exists ───────────────


def _lock(repo: Path, *, minutes_ago: float, sid: str = "abcdef0123456789") -> None:
    import json
    from datetime import datetime, timedelta, timezone

    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    (repo / ".dev").mkdir(exist_ok=True)
    (repo / ".dev" / "agent.lock").write_text(
        json.dumps({"session_id": sid, "branch": "feat/other", "claimed_at": ts, "updated_at": ts}),
        encoding="utf-8",
    )


def test_a_fresh_lock_reads_as_live_with_its_age_and_the_worktree_route(hook, repo: Path) -> None:
    """Before: "another session may be active" for a 7-minute lock and a 3-hour one alike,
    so the reader had to run `navig repo lock status` to learn which. The age and the
    verdict are what an agent needs before its first edit."""
    _lock(repo, minutes_ago=7)
    out = hook.briefing(repo)
    assert "agent lock LIVE" in out
    assert "abcdef01" in out and "feat/other" in out
    assert "touched 7m ago" in out
    assert "navig repo new" in out  # the route, not just the warning


def test_an_expired_lock_reads_as_expired_and_says_the_next_edit_takes_over(hook, repo: Path) -> None:
    _lock(repo, minutes_ago=180)
    out = hook.briefing(repo)
    assert "agent lock EXPIRED" in out
    assert "3.0h ago" in out
    assert "takes the lock over" in out
    assert "LIVE" not in out


def test_the_verdict_flips_exactly_at_the_ttl(hook) -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    just_inside = {"branch": "b", "updated_at": (now - timedelta(minutes=hook.LOCK_TTL_MINUTES - 1)).isoformat()}
    just_past = {"branch": "b", "updated_at": (now - timedelta(minutes=hook.LOCK_TTL_MINUTES + 1)).isoformat()}
    assert "LIVE" in hook._lock_line(just_inside, "s", now=now)
    assert "EXPIRED" in hook._lock_line(just_past, "s", now=now)


def test_an_unreadable_timestamp_is_treated_as_dead_not_live(hook) -> None:
    """agent_lock.py treats a corrupt timestamp as a dead lock (it claims). The briefing
    must not contradict the hook that actually enforces the rule."""
    line = hook._lock_line({"branch": "b", "updated_at": "not-a-date"}, "s")
    assert "treat it as dead" in line
    assert "LIVE" not in line


def test_the_ttl_agrees_with_the_hook_that_enforces_it_and_the_cli(hook) -> None:
    """Three copies of one number (this briefing, agent_lock.py, repo.py) — each says
    "keep in sync" in a comment, which is a claim. Assert it."""
    import importlib.util
    import re

    spec = importlib.util.spec_from_file_location(
        "agent_lock_hook", _REPO_ROOT / "scripts" / "agent-hooks" / "agent_lock.py"
    )
    agent_lock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent_lock)
    assert hook.LOCK_TTL_MINUTES == agent_lock.TTL_MINUTES

    repo_py = (_REPO_ROOT / "core" / "navig" / "commands" / "repo.py").read_text(encoding="utf-8")
    m = re.search(r"^LOCK_TTL_MINUTES\s*=\s*(\d+)", repo_py, re.M)
    assert m, "repo.py no longer declares LOCK_TTL_MINUTES at module level"
    assert int(m.group(1)) == hook.LOCK_TTL_MINUTES
