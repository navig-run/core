"""The SessionStart briefing must return within its budget, whatever the repo costs.

Regression for 2026-10-03: with 16 worktrees on a loaded box a full briefing
took 366 s, so the hook hit its 30 s ceiling on every session start — no
briefing at all, plus 30 s added to a Claude startup the VS Code extension caps
at 60 s ("Subprocess initialization did not complete within 60000ms").
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from navig.guard import session_start as S


class _DoneProc:
    def wait(self, timeout=None):
        return 0


class _HungProc:
    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("refresh", timeout)


def test_cache_round_trip_is_atomic(tmp_path: Path) -> None:
    S._write_cache(tmp_path, "[repo-guard] clean")
    text, mtime = S._read_cache(tmp_path)
    assert text == "[repo-guard] clean" and mtime > 0
    assert [p.name for p in S._cache_path(tmp_path).parent.iterdir()] == [S.CACHE_NAME]


def test_cached_copy_is_marked_with_its_age_on_the_first_line_only() -> None:
    out = S._cached_with_age("[repo-guard] header\n* line", mtime=1000.0, now=1000.0 + 600)
    first, second = out.splitlines()
    assert first.startswith("[repo-guard] header") and "as of 10m ago" in first
    assert second == "* line"


def test_fast_refresh_returns_the_fresh_briefing(tmp_path: Path, monkeypatch) -> None:
    def spawn(root, token=None):
        S.refresh(root, token)  # finishes inside the budget
        return _DoneProc()

    monkeypatch.setattr(S, "briefing", lambda root: "[repo-guard] fresh")
    monkeypatch.setattr(S, "_spawn_refresh", spawn)
    assert S.bounded_briefing(tmp_path, budget_s=1) == "[repo-guard] fresh"


def test_slow_refresh_serves_the_cache_with_its_age(tmp_path: Path, monkeypatch) -> None:
    S._write_cache(tmp_path, "[repo-guard] yesterday")
    old = time.time() - 3600
    os.utime(S._cache_path(tmp_path), (old, old))
    monkeypatch.setattr(S, "_spawn_refresh", lambda root, token=None: _HungProc())
    started = time.monotonic()
    out = S.bounded_briefing(tmp_path, budget_s=0.1)
    assert time.monotonic() - started < 5
    assert out.startswith("[repo-guard] yesterday") and "as of 60m ago" in out


def test_first_run_without_cache_points_at_the_full_tool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(S, "_spawn_refresh", lambda root, token=None: _HungProc())
    assert "navig repo stale" in S.bounded_briefing(tmp_path, budget_s=0.1)


def test_one_refresher_at_a_time_and_a_dead_one_is_reclaimed(tmp_path: Path, monkeypatch) -> None:
    assert S._claim_refresh(tmp_path) is not None
    assert S._claim_refresh(tmp_path) is None  # ten sessions open together: one refresh
    monkeypatch.setattr(S, "REFRESH_LOCK_STALE_S", -1)  # the holder died long ago
    token = S._claim_refresh(tmp_path)
    assert token is not None
    S._release_refresh(tmp_path, token)
    assert S._claim_refresh(tmp_path) is not None


def test_a_reclaimed_refresher_cannot_release_its_successors_lock(tmp_path: Path, monkeypatch) -> None:
    """A slow refresher whose lock went stale must not delete the next one's (F2485)."""
    lock = S._cache_path(tmp_path).with_suffix(".refresh.lock")
    slow = S._claim_refresh(tmp_path)
    monkeypatch.setattr(S, "REFRESH_LOCK_STALE_S", -1)
    successor = S._claim_refresh(tmp_path)  # reclaims the "dead" lock
    assert slow and successor and slow != successor
    monkeypatch.setattr(S, "briefing", lambda root: "[repo-guard] late")
    S.refresh(tmp_path, slow)  # the slow one finishes at last
    assert lock.read_text(encoding="ascii") == successor
    S._release_refresh(tmp_path, successor)
    assert not lock.exists()


def test_a_failed_spawn_releases_the_refresh_claim(tmp_path: Path, monkeypatch) -> None:
    """No child, no release: every later session would wait out the stale window (F2484)."""

    def refuse(*args, **kwargs):
        raise OSError("interpreter cannot be launched")

    monkeypatch.setattr(S.subprocess, "Popen", refuse)
    assert "navig repo stale" in S.bounded_briefing(tmp_path, budget_s=0.1)
    assert not S._cache_path(tmp_path).with_suffix(".refresh.lock").exists()


def test_a_busy_refresher_means_cache_now_not_another_spawn(tmp_path: Path, monkeypatch) -> None:
    S._write_cache(tmp_path, "[repo-guard] cached")
    assert S._claim_refresh(tmp_path)  # someone else holds the refresh
    monkeypatch.setattr(S, "_spawn_refresh", lambda root: pytest.fail("must not spawn a second refresh"))
    assert S.bounded_briefing(tmp_path, budget_s=0.1).startswith("[repo-guard] cached")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_the_real_hook_script_returns_a_briefing_and_caches_it(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a\n")
    git("add", "a.txt")
    git("commit", "-q", "-m", "base")
    hooks = tmp_path / "scripts" / "agent-hooks"
    hooks.mkdir(parents=True)
    shutil.copy(S.__file__, hooks / "session_start.py")

    res = subprocess.run(
        [sys.executable, str(hooks / "session_start.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0
    assert res.stdout.startswith("[repo-guard]")
    deadline = time.time() + 30  # the detached refresh releases its lock when done
    while S._cache_path(tmp_path).with_suffix(".refresh.lock").exists() and time.time() < deadline:
        time.sleep(0.2)
    assert S._read_cache(tmp_path) is not None
