"""Tests for orphaned-worktree detection + ``navig repo prune``.

An orphan is a physical dir under ``.dev/worktrees/`` that git no longer tracks
— what ``git worktree remove`` leaves behind on Windows when a live handle blocks
the delete. It is invisible to ``git worktree list`` (and so to the rest of
``stale``); ``prune`` cleans it. Throwaway git repos under tmp_path only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.repo import (
    _orphan_kind,
    collect_stale,
    orphan_worktree_dirs,
    repo_app,
)

runner = CliRunner()


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


def _registered_worktree(root: Path, slug: str, branch: str) -> Path:
    wt = root / ".dev" / "worktrees" / slug
    _git("worktree", "add", str(wt), "-b", branch, cwd=root)
    return wt


def _orphan_dir(root: Path, name: str, *, checkout: bool = False) -> Path:
    """A physical dir under .dev/worktrees that git does NOT track."""
    d = root / ".dev" / "worktrees" / name
    d.mkdir(parents=True)
    (d / "note.txt").write_text("leftover\n", encoding="utf-8")
    if checkout:
        (d / ".git").write_text("gitdir: /dangling\n", encoding="utf-8")
    return d


# ── detection ────────────────────────────────────────────────────────────────


def test_no_orphans_when_worktrees_home_absent(repo: Path) -> None:
    assert orphan_worktree_dirs(repo) == []


def test_orphan_detected_and_registered_excluded(repo: Path) -> None:
    _registered_worktree(repo, "live", "feat/live")
    _orphan_dir(repo, "dead-shell")
    _orphan_dir(repo, "dead-checkout", checkout=True)

    orphans = orphan_worktree_dirs(repo)
    assert {o["name"] for o in orphans} == {"dead-checkout", "dead-shell"}  # 'live' excluded
    by_name = {o["name"]: o for o in orphans}
    assert by_name["dead-checkout"]["checkout"] is True
    assert by_name["dead-shell"]["checkout"] is False


def test_collect_stale_includes_orphan_dirs(repo: Path) -> None:
    _orphan_dir(repo, "leftover")
    data = collect_stale(repo)
    assert [o["name"] for o in data["orphan_dirs"]] == ["leftover"]


def test_entries_and_kind_are_honest_about_content(repo: Path) -> None:
    """A stripped-.git leftover that still holds a heavy tree must NOT read as empty."""
    _orphan_dir(repo, "shell")  # 1 entry (note.txt)
    (repo / ".dev" / "worktrees" / "empty").mkdir(parents=True)  # 0 entries
    heavy = repo / ".dev" / "worktrees" / "heavy"  # looks like a real checkout
    for sub in ("core", "apps", "plugins", "docs"):
        (heavy / sub).mkdir(parents=True)

    by_name = {o["name"]: o for o in orphan_worktree_dirs(repo)}
    assert by_name["empty"]["entries"] == 0
    assert _orphan_kind(by_name["empty"]) == "[dim]empty[/dim]"
    assert by_name["shell"]["entries"] == 1
    assert _orphan_kind(by_name["shell"]) == "[dim]leftover[/dim]"
    assert by_name["heavy"]["entries"] >= 4
    assert "checkout" in _orphan_kind(by_name["heavy"])  # honest: heavy dir flagged, not "empty"


# ── prune ────────────────────────────────────────────────────────────────────


def test_prune_dry_run_lists_without_deleting(repo: Path) -> None:
    d = _orphan_dir(repo, "junk")
    result = runner.invoke(repo_app, ["prune", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["dry_run"] is True
    assert [o["name"] for o in data["orphans"]] == ["junk"]
    assert data["removed"] == []
    assert d.exists()  # untouched by a dry run


def test_prune_yes_deletes_orphans(repo: Path) -> None:
    d1 = _orphan_dir(repo, "junk1")
    d2 = _orphan_dir(repo, "junk2", checkout=True)
    result = runner.invoke(repo_app, ["prune", "--repo", str(repo), "--yes", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert set(data["removed"]) == {"junk1", "junk2"}
    assert data["skipped"] == []
    assert not d1.exists() and not d2.exists()


def test_prune_never_touches_a_registered_worktree(repo: Path) -> None:
    live = _registered_worktree(repo, "live", "feat/live")
    _orphan_dir(repo, "junk")
    result = runner.invoke(repo_app, ["prune", "--repo", str(repo), "--yes", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["removed"] == ["junk"]
    assert live.exists()  # registered worktree survives the prune
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout
    assert "live" in out  # still a tracked worktree


def test_prune_reports_clean_when_nothing_orphaned(repo: Path) -> None:
    result = runner.invoke(repo_app, ["prune", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["orphans"] == [] and data["removed"] == []


# ── safety: never delete a live worktree without --force ──────────────────────


def _live_worktree_orphan(root: Path, name: str, *, dirty: bool = True) -> Path:
    """A .dev/worktrees dir that is its OWN live git repo (uncommitted work at risk)."""
    d = root / ".dev" / "worktrees" / name
    d.mkdir(parents=True)
    _git("init", "-b", "wtbranch", cwd=d)
    _git("config", "user.email", "test@navig.local", cwd=d)
    _git("config", "user.name", "navig-test", cwd=d)
    (d / "base.txt").write_text("base\n", encoding="utf-8")
    _git("add", "base.txt", cwd=d)
    _git("commit", "-m", "base", cwd=d)  # born branch
    if dirty:
        (d / "work.txt").write_text("uncommitted work\n", encoding="utf-8")  # untracked -> dirty
    return d


def test_prune_skips_live_worktree_without_force(repo: Path) -> None:
    live = _live_worktree_orphan(repo, "live-wt")
    _orphan_dir(repo, "dead")  # a plain dead leftover
    result = runner.invoke(repo_app, ["prune", "--repo", str(repo), "--yes", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["removed"] == ["dead"]  # dead leftover deleted...
    assert any(  # ...but the live worktree is preserved with a clear reason
        s["name"] == "live-wt" and "live worktree" in s["reason"] for s in data["skipped"]
    )
    assert live.is_dir()  # its uncommitted work is untouched


def test_prune_force_deletes_live_worktree(repo: Path) -> None:
    live = _live_worktree_orphan(repo, "live-wt")
    result = runner.invoke(
        repo_app, ["prune", "--repo", str(repo), "--yes", "--force", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["removed"] == ["live-wt"]
    assert not live.exists()


def test_prune_dry_run_marks_live_vs_dead(repo: Path) -> None:
    _live_worktree_orphan(repo, "live-wt")
    _orphan_dir(repo, "dead")
    result = runner.invoke(repo_app, ["prune", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    by_name = {o["name"]: o for o in data["orphans"]}
    assert by_name["live-wt"]["live"] is not None  # flagged as a live worktree
    assert by_name["live-wt"]["live"]["branch"] == "wtbranch"
    assert by_name["live-wt"]["live"]["dirty"] is True
    assert by_name["dead"]["live"] is None  # dead leftover — safe
    assert data["removed"] == []  # dry run deletes nothing


# ── _rmtree_force retries transient (Windows scanner) locks ───────────────────


def test_rmtree_force_retries_then_succeeds(tmp_path: Path, monkeypatch) -> None:
    import shutil as _shutil

    from navig.commands.repo import _rmtree_force

    d = tmp_path / "x"
    d.mkdir()
    real = _shutil.rmtree
    calls = {"n": 0}

    def flaky(path, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("in use")  # transient lock on the first two tries
        real(path, **kw)

    monkeypatch.setattr(_shutil, "rmtree", flaky)
    assert _rmtree_force(d, attempts=3, base_delay=0) is None
    assert calls["n"] == 3 and not d.exists()


def test_rmtree_force_gives_up_after_attempts(tmp_path: Path, monkeypatch) -> None:
    import shutil as _shutil

    from navig.commands.repo import _rmtree_force

    d = tmp_path / "y"
    d.mkdir()

    def always_fail(path, **kw):
        raise OSError("locked")

    monkeypatch.setattr(_shutil, "rmtree", always_fail)
    err = _rmtree_force(d, attempts=2, base_delay=0)
    assert err and "locked" in err
    assert d.exists()  # left in place for a later retry


# ── navig repo remove: reliable single-worktree cleanup ───────────────────────


def test_remove_deletes_registered_worktree(repo: Path) -> None:
    wt = _registered_worktree(repo, "gone", "feat/gone")
    assert wt.is_dir()
    result = runner.invoke(repo_app, ["remove", "gone", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["removed"] is True
    assert not wt.exists()
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout
    assert "gone" not in out  # unregistered too


def test_remove_rejects_non_worktree(repo: Path) -> None:
    result = runner.invoke(repo_app, ["remove", "nope", "--repo", str(repo)])
    assert result.exit_code == 1
    assert "not a registered worktree" in result.output.lower()


def test_remove_refuses_dirty_without_force(repo: Path) -> None:
    wt = _registered_worktree(repo, "dirty", "feat/dirty")
    (wt / "f.txt").write_text("modified\n", encoding="utf-8")  # tracked change -> dirty
    result = runner.invoke(repo_app, ["remove", "dirty", "--repo", str(repo)])
    assert result.exit_code == 1
    assert wt.is_dir()  # uncommitted work preserved
    forced = runner.invoke(repo_app, ["remove", "dirty", "--repo", str(repo), "--force", "--json"])
    assert forced.exit_code == 0, forced.output
    assert not wt.exists()
