"""The session briefing must make set-aside work impossible to miss.

Regression for Schema, 2026-09-28: a session committed a checkout's uncommitted
work to ``wip/main-checkout-2026-09-28``, reset main, and ended. The briefing
listed it as one quiet ``* branch not merged`` line and ~240 files stayed off
main for a day.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from navig.guard import session_start

# A real repo plus a full session briefing per test: 5-12 s idle, past the gate's 30 s
# per-test ceiling under load, where pytest-timeout os._exit()s the xdist worker and the
# rescheduling crash aborts the whole run.
pytestmark = pytest.mark.timeout(240)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "base")
    for branch, fname in (("wip/main-checkout-2026-09-28", "w.txt"), ("feat/thing", "f.txt")):
        _git(tmp_path, "checkout", "-q", "-b", branch, "main")
        (tmp_path / fname).write_text("x\n")
        _git(tmp_path, "add", fname)
        _git(tmp_path, "commit", "-q", "-m", branch)
    _git(tmp_path, "checkout", "-q", "main")
    return tmp_path


def test_set_aside_branch_is_loud_and_sized(repo: Path) -> None:
    text = session_start.briefing(repo)
    line = next(
        (l for l in text.splitlines() if "wip/main-checkout-2026-09-28" in l), ""
    )
    assert line.startswith("[!] SET-ASIDE WORK not restored"), text
    assert "1 commit(s), 1 file(s)" in line


def test_ordinary_feature_branch_stays_a_quiet_line(repo: Path) -> None:
    text = session_start.briefing(repo)
    assert "* branch not merged into main: feat/thing" in text
    assert "SET-ASIDE WORK not restored: feat/thing" not in text


def test_restored_set_aside_branch_is_not_flagged(repo: Path) -> None:
    _git(repo, "merge", "-q", "--no-edit", "wip/main-checkout-2026-09-28")
    text = session_start.briefing(repo)
    assert "SET-ASIDE WORK not restored" not in text


def test_the_restore_command_applies_every_set_aside_commit(repo: Path) -> None:
    """A bare branch name cherry-picks only its tip (review F2352): restore the RANGE."""
    _git(repo, "checkout", "-q", "wip/main-checkout-2026-09-28")
    (repo / "w2.txt").write_text("y\n")
    _git(repo, "add", "w2.txt")
    _git(repo, "commit", "-q", "-m", "second")
    _git(repo, "checkout", "-q", "main")
    line = next(
        l for l in session_start.briefing(repo).splitlines() if "wip/main-checkout-2026-09-28" in l
    )
    assert "2 commit(s)" in line
    assert "git cherry-pick --no-commit main..wip/main-checkout-2026-09-28" in line
