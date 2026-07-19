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
    # NOT tmp_path: pytest.ini sets --basetemp=.pytest_tmp, which lives INSIDE
    # this repo — "outside a git repo" needs the system temp dir instead.
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
