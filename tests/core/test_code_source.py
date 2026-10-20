"""Which code the daemon runs — and the warning when it is not a clean ``main``.

The daemon runs the main checkout's editable install, so a feature branch or uncommitted
edits left there ARE the live bot. These tests drive ``describe`` against real throwaway
git repositories, not mocks: the answer comes from ``git`` itself.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from navig.core.code_source import CodeSource, describe, summary

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(autouse=True)
def _git_stops_at_tmp(tmp_path: Path, monkeypatch) -> None:
    """tmp_path can sit INSIDE this very repository; stop git walking up past it."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repository on ``main`` with one commit and a ``pkg/`` folder standing in for navig."""
    root = tmp_path / "checkout"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "pkg/__init__.py")
    _git(root, "commit", "-q", "-m", "init")
    return root


def test_a_clean_main_checkout_is_ok(repo: Path) -> None:
    src = describe(repo / "pkg")
    assert src.kind == "checkout"
    assert src.branch == "main"
    assert src.ok, src.warnings
    assert "main @" in summary(src)


def test_a_feature_branch_warns(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feat/unfinished")
    src = describe(repo / "pkg")
    assert not src.ok
    assert any("feat/unfinished" in w for w in src.warnings)


def test_uncommitted_edits_warn(repo: Path) -> None:
    (repo / "pkg" / "__init__.py").write_text("x = 2\n", encoding="utf-8")
    src = describe(repo / "pkg")
    assert src.dirty == 1
    assert any("uncommitted" in w for w in src.warnings)


def test_untracked_files_alone_do_not_warn(repo: Path) -> None:
    (repo / "scratch.txt").write_text("notes", encoding="utf-8")
    assert describe(repo / "pkg").ok


def test_an_installed_package_is_ok(tmp_path: Path) -> None:
    pkg = tmp_path / "site-packages" / "navig"
    pkg.mkdir(parents=True)
    src = describe(pkg)
    assert src.kind == "package"
    assert src.ok


def test_the_doctor_row_warns_never_a_green_tick_over_a_branch(monkeypatch) -> None:
    from navig.commands import doctor
    from navig.core import code_source

    monkeypatch.setattr(
        code_source,
        "describe",
        lambda: CodeSource(kind="checkout", root="r", branch="feat/x", warnings=["runs branch 'feat/x'"]),
    )
    row = doctor.check_running_code()[0]
    assert row[1] is False
    assert "feat/x" in row.detail


def test_the_doctor_row_warns_when_the_check_itself_fails(monkeypatch) -> None:
    from navig.commands import doctor
    from navig.core import code_source

    def _boom():
        raise RuntimeError("no git")

    monkeypatch.setattr(code_source, "describe", _boom)
    row = doctor.check_running_code()[0]
    assert row[1] is False
    assert "could not check" in row.detail


def _git_fails(monkeypatch, exc: BaseException) -> None:
    from navig.core import code_source

    def _run(*_a, **_k):
        raise exc

    monkeypatch.setattr(code_source.subprocess, "run", _run)


def test_a_checkout_whose_git_times_out_warns_never_reads_as_a_package(repo: Path, monkeypatch) -> None:
    """Live 2026-10-11: a loaded boot timed git out and the log said 'installed package' (INFO)."""
    _git_fails(monkeypatch, subprocess.TimeoutExpired(cmd="git", timeout=15))
    src = describe(repo / "pkg")
    assert src.kind == "checkout"
    assert Path(src.root) == repo
    assert not src.ok
    assert "timed out" in summary(src)
    assert "installed package" not in summary(src)


def test_a_checkout_with_git_missing_warns(repo: Path, monkeypatch) -> None:
    _git_fails(monkeypatch, FileNotFoundError("git"))
    src = describe(repo / "pkg")
    assert src.kind == "checkout" and not src.ok
    assert "could not run" in summary(src)


def test_an_installed_package_stays_ok_when_git_is_missing(tmp_path: Path, monkeypatch) -> None:
    pkg = tmp_path / "site-packages" / "navig"
    pkg.mkdir(parents=True)
    _git_fails(monkeypatch, FileNotFoundError("git"))
    src = describe(pkg)
    assert src.kind == "package"
    assert src.ok


def test_a_wheel_in_a_venv_inside_a_repo_is_still_a_package(repo: Path, monkeypatch) -> None:
    pkg = repo / ".venv" / "Lib" / "site-packages" / "navig"
    pkg.mkdir(parents=True)
    _git_fails(monkeypatch, subprocess.TimeoutExpired(cmd="git", timeout=15))
    src = describe(pkg)
    assert src.kind == "package"
    assert src.ok


def _fail_only(monkeypatch, needle: str, exc: BaseException | None = None) -> None:
    """Make exactly the git subcommand containing *needle* fail; others run for real."""
    from navig.core import code_source

    real = code_source.subprocess.run

    def _run(argv, *a, **k):
        if needle in " ".join(argv):
            raise exc or subprocess.TimeoutExpired(cmd="git", timeout=15)
        return real(argv, *a, **k)

    monkeypatch.setattr(code_source.subprocess, "run", _run)


def test_a_failed_status_probe_warns_and_is_never_clean(repo: Path, monkeypatch) -> None:
    """Live 2026-10-11 follow-up: show-toplevel answered, a later probe timed out."""
    _fail_only(monkeypatch, "status")
    src = describe(repo / "pkg")
    assert src.kind == "checkout" and src.dirty == 0
    assert not src.ok
    assert "could not read uncommitted edits" in summary(src)


def test_a_failed_branch_probe_warns(repo: Path, monkeypatch) -> None:
    _fail_only(monkeypatch, "abbrev-ref")
    src = describe(repo / "pkg")
    assert not src.ok
    assert "could not read its branch" in summary(src)


def test_a_failed_commit_probe_warns(repo: Path, monkeypatch) -> None:
    _fail_only(monkeypatch, "--short")
    src = describe(repo / "pkg")
    assert not src.ok
    assert "could not read its commit" in summary(src)


def test_a_failed_behind_probe_warns(repo: Path, monkeypatch) -> None:
    _fail_only(monkeypatch, "rev-list")
    src = describe(repo / "pkg")
    assert not src.ok
    assert "could not tell how far behind" in summary(src)


def test_a_repo_without_origin_main_is_unknown_not_behind_and_ok(repo: Path) -> None:
    """No fetched remote is an expected unknown — the checkout is still healthy."""
    src = describe(repo / "pkg")
    assert src.behind is None
    assert src.ok, src.warnings


def test_a_fetched_origin_reports_the_real_behind_count(repo: Path, tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    bare.mkdir()
    _git(bare, "init", "-q", "--bare")
    _git(repo, "remote", "add", "origin", str(bare))
    (repo / "pkg" / "more.py").write_text("y = 2\n", encoding="utf-8")
    _git(repo, "add", "pkg/more.py")
    _git(repo, "commit", "-q", "-m", "second")
    _git(repo, "push", "-q", "origin", "main")
    # Rewind local so origin/main is AHEAD by one — that is what "behind" counts.
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    src = describe(repo / "pkg")
    assert src.behind == 1
    assert not src.ok
    assert "1 commit(s) behind" in summary(src)