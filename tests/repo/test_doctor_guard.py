"""Tests for the ``navig doctor`` Repo Guard check (check_repo_guard).

The check is repo-relative: silent outside git, informational when the guard
is absent, warning on partial wiring, green with lock detail when active.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from navig.commands.doctor import check_repo_guard
from navig.commands.repo import guard_install_cmd


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "test@navig.local", cwd=root)
    _git("config", "user.name", "navig-test", cwd=root)
    (root / "a.txt").write_text("x\n", encoding="utf-8")
    _git("add", "a.txt", cwd=root)
    _git("commit", "-m", "base", cwd=root)
    return root


def test_outside_git_repo_is_silent(monkeypatch) -> None:
    # NOT tmp_path: core/conftest.py points PYTEST_DEBUG_TEMPROOT at core/.dev/tmp, which
    # lives INSIDE this repo — "outside a git repo" needs the system temp dir instead.
    import tempfile

    with tempfile.TemporaryDirectory() as outside:
        monkeypatch.chdir(outside)
        result = check_repo_guard()
        monkeypatch.chdir(Path(__file__).parent)  # step out so Windows can delete the dir
    assert result == []


def test_uninstalled_guard_is_informational_not_failure(repo: Path, monkeypatch) -> None:
    monkeypatch.chdir(repo)
    results = check_repo_guard()
    assert len(results) == 1
    _icon, ok, line = results[0]
    assert ok is True  # absence must never fail doctor
    assert "guard install" in line


def test_installed_guard_reports_active_and_lock_free(repo: Path, monkeypatch) -> None:
    guard_install_cmd(repo=str(repo))
    monkeypatch.chdir(repo)
    results = check_repo_guard()
    _icon, ok, line = results[0]
    assert ok is True
    assert "active" in line and "lock free" in line


def test_an_outdated_hook_script_turns_the_row_into_a_warning(repo: Path, monkeypatch) -> None:
    """Wired is not current. The row used to check the events and the lock and render ✓
    over hook scripts of any age — a repo fully wired to the pre-#1481 agent_lock.py (the
    one that blocked `navig repo new` from a worktree) read "active". A green light that
    does not consult the script state is the "green over unknown" trap."""
    guard_install_cmd(repo=str(repo))
    hook = repo / ".claude" / "hooks" / "agent_lock.py"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# an older build\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    _icon, ok, line = check_repo_guard()[0]
    assert ok is False, "an outdated hook script must not render as a green tick"
    assert "agent_lock.py outdated" in line
    assert "guard install" in line
    assert "active" in line, "the wiring IS active — say so alongside the warning, not instead of it"


def test_a_missing_hook_script_behind_live_wiring_warns(repo: Path, monkeypatch) -> None:
    guard_install_cmd(repo=str(repo))
    (repo / ".claude" / "hooks" / "session_start.py").unlink()
    monkeypatch.chdir(repo)
    _icon, ok, line = check_repo_guard()[0]
    assert ok is False
    assert "session_start.py missing" in line


def test_partial_wiring_warns(repo: Path, monkeypatch) -> None:
    guard_install_cmd(repo=str(repo))
    settings_path = repo / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    del settings["hooks"]["SessionStart"]  # half-wired guard = false safety
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    monkeypatch.chdir(repo)
    results = check_repo_guard()
    _icon, ok, line = results[0]
    assert ok is False
    assert "partially wired" in line and "SessionStart" in line


def test_orphaned_worktree_dirs_add_a_warn_row(repo: Path, monkeypatch) -> None:
    guard_install_cmd(repo=str(repo))
    (repo / ".dev" / "worktrees" / "dead").mkdir(parents=True)  # git-untracked leftover
    monkeypatch.chdir(repo)
    results = check_repo_guard()

    assert any("active" in line for _i, _o, line in results)  # guard row still present
    orphan_rows = [(ok, line) for _i, ok, line in results if "orphaned dir" in line]
    assert len(orphan_rows) == 1
    ok, line = orphan_rows[0]
    assert ok is False and "navig repo prune" in line


def test_no_orphan_row_when_clean(repo: Path, monkeypatch) -> None:
    guard_install_cmd(repo=str(repo))
    monkeypatch.chdir(repo)
    results = check_repo_guard()
    assert not any("orphaned dir" in line for _i, _o, line in results)  # silent when clean
