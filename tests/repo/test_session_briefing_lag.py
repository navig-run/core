"""The SessionStart briefing warns when local <default> is behind origin/<default>.

The staleness at the root of the whole shared-checkout class: agents merge through
GitHub, so origin/main advances while the local branch sits still. A session started
in that checkout runs stale code and judges "merged" against a ref 100+ commits old
(measured). The briefing now says so, using the tracking ref already on disk — the hook
fetches nothing, so a stale count is free, offline, and better than silence.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[3] / "scripts" / "agent-hooks" / "session_start.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("session_start_hook_lag", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(root: Path, name: str) -> None:
    (root / name).write_text("x\n", encoding="utf-8")
    _git("add", name, cwd=root)
    _git("commit", "-q", "-m", f"add {name}", cwd=root)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@navig.local", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    _commit(root, "base.txt")
    _git("remote", "add", "origin", str(bare), cwd=root)
    _git("push", "-q", "-u", "origin", "main", cwd=root)
    return root


def _advance_remote(repo: Path, n: int) -> None:
    """Push n commits to origin/main from a second clone, leaving THIS repo's local behind."""
    clone = repo.parent / "pusher"
    _git("clone", "-q", str(repo.parent / "origin.git"), str(clone), cwd=repo.parent)
    _git("config", "user.email", "t@navig.local", cwd=clone)
    _git("config", "user.name", "t", cwd=clone)
    for i in range(n):
        _commit(clone, f"remote{i}.txt")
    _git("push", "-q", "origin", "main", cwd=clone)
    _git("fetch", "-q", "origin", cwd=repo)  # updates the tracking ref, NOT local main


def test_no_line_when_local_is_current(hook, repo: Path):
    assert "behind origin" not in hook.briefing(repo)


def test_the_lag_line_fires_and_counts(hook, repo: Path):
    _advance_remote(repo, 3)

    text = hook.briefing(repo)

    assert "local main is 3 commit(s) behind origin/main" in text
    assert "runs stale code" in text


def test_the_lag_line_is_ascii(hook, repo: Path):
    _advance_remote(repo, 2)

    assert hook.briefing(repo).isascii()


def test_no_remote_no_line(hook, tmp_path: Path):
    """A repo with no origin has no tracking ref to compare against — no line, no crash."""
    root = tmp_path / "lonely"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@navig.local", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    _commit(root, "a.txt")

    assert "behind origin" not in hook.briefing(root)


def test_the_lag_line_survives_a_broken_venv(tmp_path: Path, repo: Path):
    """Same contract as the rest of the hook: stdlib + git only. Run as a subprocess
    with navig un-importable and confirm the lag line still prints."""
    import os
    import sys

    _advance_remote(repo, 4)
    shadow = tmp_path / "nonavig"
    shadow.mkdir()
    (shadow / "navig.py").write_text('raise ImportError("broken venv")\n', encoding="utf-8")
    hook_copy = repo / "scripts" / "agent-hooks" / "session_start.py"
    hook_copy.parent.mkdir(parents=True)
    hook_copy.write_text(_HOOK.read_text(encoding="utf-8"), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(hook_copy)], cwd=str(repo), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(shadow), "PYTHONUTF8": "1"}, timeout=60, encoding="utf-8",
    )

    assert res.returncode == 0
    assert "local main is 4 commit(s) behind origin/main" in res.stdout
