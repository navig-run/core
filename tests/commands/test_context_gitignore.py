"""`navig context init` must gitignore `.navig/` even when run from a repo SUBDIRECTORY.

The old code checked `(cwd / ".git").exists()` — False in a subdir — so `.navig/` was left
un-ignored and could be committed by accident. It now uses `git rev-parse --show-toplevel`
(walks up) and writes `.navig/` to the repo ROOT .gitignore (the pattern matches at any depth).
"""

from __future__ import annotations

import subprocess

from navig.commands.context import _ensure_navig_gitignored, _git_toplevel


def _git_init(path):
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, text=True, timeout=10)


# ------------------------------------------------------------------- _git_toplevel


def test_git_toplevel_finds_root_from_subdir(tmp_path):
    _git_init(tmp_path)
    sub = tmp_path / "services" / "api"
    sub.mkdir(parents=True)
    root = _git_toplevel(sub)
    assert root is not None
    assert (root / ".git").exists()  # the returned dir IS the repo root, reached from a subdir


def test_git_toplevel_none_when_git_says_not_a_repo(monkeypatch):
    # NB: pytest's tmp_path lives INSIDE the navig repo (.dev/tmp/), so a raw tmp dir would
    # resolve UP to the navig repo — mock git to get a deterministic "not a repo".
    from pathlib import Path

    class _R:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    assert _git_toplevel(Path("/anywhere")) is None


# ------------------------------------------------------------- _ensure_navig_gitignored


def test_ensure_gitignored_from_subdir_writes_the_repo_root(tmp_path):
    _git_init(tmp_path)
    sub = tmp_path / "svc" / "api"
    sub.mkdir(parents=True)

    written = _ensure_navig_gitignored(sub)

    assert written is not None
    assert written.name == ".gitignore"
    assert (written.parent / ".git").exists(), "must be the ROOT .gitignore, not the subdir's"
    assert ".navig/" in written.read_text(encoding="utf-8")
    assert not (sub / ".gitignore").exists(), "must not create a .gitignore in the subdir"


def test_ensure_gitignored_appends_without_clobbering(tmp_path):
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    written = _ensure_navig_gitignored(tmp_path)

    assert written is not None
    content = written.read_text(encoding="utf-8")
    assert "node_modules/" in content and ".navig/" in content  # appended, not overwritten


def test_ensure_gitignored_skips_when_already_present(tmp_path):
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text(".navig/\n", encoding="utf-8")

    assert _ensure_navig_gitignored(tmp_path) is None
    # left untouched — no duplicate entry
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == ".navig/\n"


def test_ensure_gitignored_none_when_not_in_a_repo(monkeypatch, tmp_path):
    # Simulate "not inside any git repo" deterministically (pytest tmp is inside the navig
    # repo). Must return None and write NOTHING.
    monkeypatch.setattr("navig.commands.context._git_toplevel", lambda _p: None)
    assert _ensure_navig_gitignored(tmp_path) is None
    assert not (tmp_path / ".gitignore").exists()
