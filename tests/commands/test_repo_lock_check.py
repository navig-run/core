"""`navig repo lock check` — the exit code a pre-commit hook reads.

The lock itself has existed for a while (`.dev/agent.lock`, claimed by the Claude Code
PreToolUse hook and reported by `navig repo lock status`). What did not exist was a way for
GIT to ask the question. Two agents in one checkout is how this repo lost a day — staged
files swept into somebody else's commit, a push refused over files its author never wrote —
so the losing session must be refused at commit time, not after the fact.

Four cases, and the two permissive ones matter as much as the two strict ones: free, stale,
and *our own* lock all pass. A guard that also fires on your own lock is a guard people
disable.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from navig.commands import repo as repo_mod
from navig.commands.repo import LOCK_RELPATH, LOCK_TTL_MINUTES, lock_app


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A directory the command treats as the repo root (the resolver is tested elsewhere)."""
    monkeypatch.setattr(repo_mod, "_require_root", lambda repo=None: tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ours-1234")
    return tmp_path


def _write_lock(root, *, session: str, minutes_ago: float, branch: str = "other/session"):
    path = root / LOCK_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    path.write_text(
        json.dumps(
            {
                "session_id": session,
                "updated_at": stamp.isoformat(),
                "branch": branch,
                "pid": 999_999,
            }
        ),
        encoding="utf-8",
    )
    return path


def _run():
    return CliRunner().invoke(lock_app, ["check"])


def test_a_free_checkout_passes(checkout):
    assert _run().exit_code == 0


def test_another_sessions_fresh_lock_refuses(checkout):
    _write_lock(checkout, session="other-9999", minutes_ago=1)
    result = _run()
    assert result.exit_code == 1
    assert "another session holds this checkout" in result.output
    assert "navig repo new" in result.output


def test_a_stale_lock_does_not_wedge_a_commit(checkout):
    """Past the TTL the holder is presumed gone — otherwise one crashed session blocks the tree."""
    _write_lock(checkout, session="other-9999", minutes_ago=LOCK_TTL_MINUTES + 5)
    assert _run().exit_code == 0


def test_our_own_lock_passes(checkout):
    """Releasing your own claim must be the routine path, never `--force`."""
    _write_lock(checkout, session="ours-1234", minutes_ago=1)
    assert _run().exit_code == 0


def test_a_corrupt_lock_reads_as_free(checkout):
    """A half-written file must not block commits; `status` reports it as unknown."""
    path = checkout / LOCK_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert _run().exit_code == 0
