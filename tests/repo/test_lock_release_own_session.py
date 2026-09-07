"""``navig repo lock release`` must not make ``--force`` the routine path.

The release rule keyed only on FRESHNESS: any lock younger than the TTL needed
``--force``, whose help text reads "another agent may be live!". But the
commonest release by far is your own — you claimed the lock by editing, you
finished, you want it gone before ending the session (the repo-guard protocol
asks for exactly that). Under the old rule that release printed a warning about
another agent and exited 1, so the way to finish cleanly was ``--force``.

That is a safety defect, not an ergonomic one. ``--force`` exists for precisely
one thing: overriding ANOTHER session's live claim, the act the guard was built
to prevent. Teaching the operator to type it in the routine case is how a
foreign lock eventually gets destroyed — and a destroyed foreign lock means two
sessions mutating one checkout, which is the original failure mode.

So: a lock whose ``session_id`` matches this session releases freely; a fresh
lock belonging to a DIFFERENT session still requires ``--force``; and a caller
with no session id at all (a human shell, CI) keeps the strict path, because
"no session" must never be mistaken for "my session".
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.repo import (
    current_session_id,
    lock_is_ours,
    lock_path,
    repo_app,
)

runner = CliRunner()

OURS = "cdd93604-c2ef-4439-9d7a-0f3ac1d8aca2"
THEIRS = "268a6cc0-a08b-430a-ae67-19a429b3a17e"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "test@navig.local", cwd=root)
    _git("config", "user.name", "navig-test", cwd=root)
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _git("add", "f.txt", cwd=root)
    _git("commit", "-m", "base", cwd=root)
    return root


def _claim(root: Path, session: str, *, when: str) -> Path:
    """Write a lock file for *session* stamped at *when* (ISO, UTC)."""
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_id": session,
                "tool": "claude-code",
                "branch": "main",
                "claimed_at": when,
                "updated_at": when,
            }
        ),
        encoding="utf-8",
    )
    return path


def _fresh() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _release(root: Path, *extra: str):
    return runner.invoke(repo_app, ["lock", "release", "--repo", str(root), *extra])


# ── identity ────────────────────────────────────────────────────────────────


def test_lock_is_ours_matches_the_env_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OURS)
    assert lock_is_ours({"session_id": OURS}) is True


def test_lock_is_ours_rejects_a_different_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OURS)
    assert lock_is_ours({"session_id": THEIRS}) is False


def test_no_session_id_is_never_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller outside Claude Code must keep the strict --force path.

    The dangerous shape would be treating "I have no session" as "the lock has
    no owner I need to respect" — that would let cron, CI, or a plain shell
    silently reap a live agent's claim.
    """
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert lock_is_ours({"session_id": THEIRS}) is False
    assert lock_is_ours({"session_id": ""}) is False
    assert current_session_id() is None


def test_empty_env_session_does_not_match_an_empty_lock_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two absent ids must not compare equal — "" == "" would be a free pass."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")
    assert lock_is_ours({"session_id": ""}) is False
    assert lock_is_ours({}) is False
    assert lock_is_ours(None) is False


# ── release behaviour ───────────────────────────────────────────────────────


def test_own_fresh_lock_releases_without_force(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OURS)
    path = _claim(repo, OURS, when=_fresh())

    result = _release(repo)

    assert result.exit_code == 0, result.output
    assert not path.exists(), "own lock should be gone"
    assert "this session" in result.output


def test_foreign_fresh_lock_still_requires_force(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The protection that matters must survive the ergonomic fix."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OURS)
    path = _claim(repo, THEIRS, when=_fresh())

    result = _release(repo)

    assert result.exit_code == 1, result.output
    assert path.exists(), "a live foreign lock must NOT be removed"
    assert "another agent may be live" in result.output


def test_foreign_fresh_lock_still_yields_to_force(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OURS)
    path = _claim(repo, THEIRS, when=_fresh())

    result = _release(repo, "--force")

    assert result.exit_code == 0, result.output
    assert not path.exists()


def test_stale_foreign_lock_still_releases_without_force(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OURS)
    path = _claim(repo, THEIRS, when="2020-01-01T00:00:00+00:00")

    result = _release(repo)

    assert result.exit_code == 0, result.output
    assert not path.exists()


def test_release_without_a_session_id_is_unchanged(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain shell sees exactly the old behaviour — nothing was loosened."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    path = _claim(repo, OURS, when=_fresh())

    result = _release(repo)

    assert result.exit_code == 1, result.output
    assert path.exists()


def test_free_lock_reports_free(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OURS)
    result = _release(repo)
    assert result.exit_code == 0, result.output
    assert "already free" in result.output
