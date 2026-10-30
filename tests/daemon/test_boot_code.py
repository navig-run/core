"""The daemon snapshots the code it booted from, so `navig doctor` can flag a daemon that
is running STALE code (source moved on disk after boot)."""

from __future__ import annotations

import re

from navig.daemon import supervisor


def test_capture_code_identity_records_git_commit_in_a_checkout():
    info = supervisor._capture_code_identity()
    assert isinstance(info, dict)
    # The test suite runs from inside the repo, so git detection must succeed.
    assert info.get("install") == "git"
    assert re.fullmatch(r"[0-9a-f]{40}", info.get("commit", "")), info
    assert info.get("branch"), info
    assert info.get("version"), info  # version is always recorded
    assert info.get("captured_at"), info


def test_capture_code_identity_never_raises(monkeypatch):
    """Boot must not fail if git is missing/hung — it degrades to a version-only identity."""
    import subprocess

    def boom(*_a, **_k):
        raise OSError("git exploded")

    monkeypatch.setattr(subprocess, "run", boom)
    info = supervisor._capture_code_identity()
    assert isinstance(info, dict)
    assert "commit" not in info  # git failed → no commit …
    assert info.get("version")  # … but the version-only identity survives


def _wheel_in_enclosing_repo(tmp_path):
    """A fake wheel install inside an UNRELATED git repo's (ignored) .venv."""
    import subprocess

    repo = tmp_path / "app"
    site = repo / ".venv" / "Lib" / "site-packages"
    (site / "navig").mkdir(parents=True)
    (site / "navig" / "__init__.py").write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git[:3], "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "app"], check=True)
    return site


def test_wheel_inside_an_unrelated_repo_is_not_a_git_install(tmp_path):
    site = _wheel_in_enclosing_repo(tmp_path)
    info = supervisor._capture_code_identity(site)
    assert info.get("install") == "pip", info
    assert "commit" not in info  # the enclosing app repo's HEAD is NOT navig's identity


def test_doctor_git_head_rejects_an_unrelated_enclosing_repo(tmp_path):
    from navig.commands import doctor

    site = _wheel_in_enclosing_repo(tmp_path)
    assert doctor._git_head(site) is None
