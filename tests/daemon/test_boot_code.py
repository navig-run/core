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
