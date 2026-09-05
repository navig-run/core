"""`navig upgrade` must detect the editable git checkout in this monorepo.

`run_upgrade` computed `is_git = (src_dir / ".git").exists()`, but `src_dir` is the editable
`core/` dir and `.git` lives at the repo ROOT — so on the operator's own install `is_git` was
False and `navig upgrade` offered a *release* upgrade (pip) instead of pulling latest commits.
Fixed by reusing `_is_navig_git_checkout` (the #533 helper). These tests pin the branch taken;
subprocess is mocked so no real git runs.
"""

from __future__ import annotations

import subprocess

from navig.commands.upgrade import run_upgrade


class _FakeGitLog:
    stdout = "abc1234 some commit message"
    stderr = ""
    returncode = 0


def test_upgrade_check_takes_git_branch_when_source_is_tracked(monkeypatch, capsys):
    monkeypatch.setattr("navig.commands.update._is_navig_git_checkout", lambda _s: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeGitLog())

    run_upgrade(check=True)

    out = capsys.readouterr().out
    # git branch → "Run navig upgrade to pull latest commits."; the pip branch says
    # "…to upgrade to the latest release." — 'commits' distinguishes them.
    assert "commits" in out
    assert "the latest release" not in out


def test_upgrade_check_takes_release_branch_for_a_wheel(monkeypatch, capsys):
    monkeypatch.setattr("navig.commands.update._is_navig_git_checkout", lambda _s: False)

    run_upgrade(check=True)

    out = capsys.readouterr().out
    assert "the latest release" in out
    assert "commits" not in out
