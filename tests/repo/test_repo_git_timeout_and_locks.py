"""``navig repo``: a timed-out git kills its TREE, and a dead add's lock does not brick ``remove``.

The outage (2026-09-15): ``navig repo new <slug>`` printed "git worktree add ... timed out
after 15s" and exited 1 -- while the checkout, which git does in a CHILD process the bare
timeout kill never reached, finished populating all 13.6k files. Git's own "initializing"
lock stayed on the worktree, so four hours later ``navig repo remove <slug>`` refused it
and blamed "uncommitted changes" for a refusal that had nothing to do with them.

Three things are pinned here: the timeout takes the whole tree down; ``list_worktrees``
reads the lock reason; and ``remove`` clears exactly the ``initializing`` lock (an add that
died) while NAMING any other lock rather than guessing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands import repo as repo_mod
from navig.commands.repo import _git, list_worktrees, repo_app

runner = CliRunner()


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run("init", "-b", "main", cwd=root)
    _run("config", "user.email", "test@navig.local", cwd=root)
    _run("config", "user.name", "navig-test", cwd=root)
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _run("add", "f.txt", cwd=root)
    _run("commit", "-m", "base", cwd=root)
    return root


def _worktree(root: Path, slug: str) -> Path:
    wt = root / ".dev" / "worktrees" / slug
    wt.parent.mkdir(parents=True, exist_ok=True)
    _run("worktree", "add", str(wt), "-b", f"feat/{slug}", cwd=root)
    return wt


# ── the timeout kills the tree ───────────────────────────────────────────────


def test_a_timeout_returns_at_the_timeout_and_nothing_of_the_tree_survives_it(
    tmp_path: Path, monkeypatch
) -> None:
    """The shape of the outage, without a 13k-file checkout: a shim `git` (a batch file
    on Windows, a shell script elsewhere) that spawns a python child holding the inherited
    pipes, exactly as `worktree add` spawns its checkout child.

    Measured against the old `subprocess.run(timeout=)`: it killed the shim at 2 s and then
    BLOCKED for the child's full 60 s waiting on those pipes, while the child ran to
    completion -- a "timed out" result over work that finished. Two things are asserted:
    the call returns AT the timeout, and the child is gone right after it.
    """
    psutil = pytest.importorskip("psutil")
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "hang.py"
    script.write_text(
        "import os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        shim = tmp_path / "git.bat"
        shim.write_text(f'@"{sys.executable}" "{script}"\r\n', encoding="ascii")
    else:
        shim = tmp_path / "git"
        shim.write_text(f'#!/bin/sh\n"{sys.executable}" "{script}"\n', encoding="utf-8")
        shim.chmod(0o755)
    monkeypatch.setattr(repo_mod, "_GIT_EXE", str(shim))

    started = time.monotonic()
    res = _git(["anything"], tmp_path, timeout=2)
    elapsed = time.monotonic() - started
    assert res.returncode == 124, (res.returncode, res.stderr)
    assert "process tree killed" in res.stderr
    assert elapsed < 20, f"returned after {elapsed:.0f}s -- it waited for the orphan to finish"
    assert pid_file.exists(), "the child never started -- the probe proves nothing"
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and psutil.pid_exists(child_pid):
        time.sleep(0.1)
    if psutil.pid_exists(child_pid):
        psutil.Process(child_pid).kill()  # never leave the probe running
        pytest.fail(f"the shim's child {child_pid} outlived the timeout")


def test_a_normal_git_result_is_unchanged(repo: Path) -> None:
    res = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    assert res.returncode == 0 and res.stdout.strip() == "main" and res.stderr == ""


# ── the lock reason is read ──────────────────────────────────────────────────


def test_list_worktrees_reads_the_lock_reason(repo: Path) -> None:
    wt = _worktree(repo, "locked-one")
    by_name = {w["path"].name: w for w in list_worktrees(repo)}
    assert by_name["locked-one"]["locked"] is None
    _run("worktree", "lock", "--reason", "initializing", str(wt), cwd=repo)
    by_name = {w["path"].name: w for w in list_worktrees(repo)}
    assert by_name["locked-one"]["locked"] == "initializing"
    _run("worktree", "unlock", str(wt), cwd=repo)
    _run("worktree", "lock", str(wt), cwd=repo)  # no reason given
    by_name = {w["path"].name: w for w in list_worktrees(repo)}
    assert by_name["locked-one"]["locked"] == ""


# ── remove: a dead add's lock is cleared; any other lock is named ────────────


def test_remove_clears_the_lock_a_dead_add_left_behind(repo: Path) -> None:
    wt = _worktree(repo, "dead-add")
    _run("worktree", "lock", "--reason", "initializing", str(wt), cwd=repo)
    result = runner.invoke(repo_app, ["remove", "dead-add", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    assert not wt.exists()
    assert all(w["path"].name != "dead-add" for w in list_worktrees(repo))


def test_remove_names_a_deliberate_lock_instead_of_blaming_uncommitted_changes(repo: Path) -> None:
    wt = _worktree(repo, "held")
    _run("worktree", "lock", "--reason", "reviewing on the laptop", str(wt), cwd=repo)
    result = runner.invoke(repo_app, ["remove", "held", "--repo", str(repo)])
    assert result.exit_code == 1
    assert "reviewing on the laptop" in result.output
    assert "uncommitted" not in result.output
    assert wt.exists()
    by_name = {w["path"].name: w for w in list_worktrees(repo)}
    assert by_name["held"]["locked"] == "reviewing on the laptop", "the lock must be left alone"


def test_remove_still_protects_uncommitted_work_after_unlocking(repo: Path) -> None:
    """Clearing the dead lock must not weaken the dirty-tree refusal."""
    wt = _worktree(repo, "dirty-dead")
    (wt / "wip.txt").write_text("unsaved\n", encoding="utf-8")
    _run("worktree", "lock", "--reason", "initializing", str(wt), cwd=repo)
    result = runner.invoke(repo_app, ["remove", "dirty-dead", "--repo", str(repo)])
    assert result.exit_code == 1
    assert "--force" in result.output and wt.exists()


# ── new: a timeout says what is on disk ──────────────────────────────────────


def test_new_reports_a_partial_checkout_on_timeout(repo: Path, monkeypatch) -> None:
    real = repo_mod._git

    def fake(args, cwd, timeout=None):
        if args[:2] == ["worktree", "add"]:
            return subprocess.CompletedProcess(
                args=["git", *args], returncode=124, stdout="", stderr="timed out"
            )
        return real(args, cwd, timeout)

    monkeypatch.setattr(repo_mod, "_git", fake)
    result = runner.invoke(repo_app, ["new", "slow-tree", "--repo", str(repo)])
    assert result.exit_code == 1
    assert "PARTIAL" in result.output
    assert "navig repo remove slow-tree --force" in result.output


def test_worktree_add_gets_a_checkout_budget_not_the_query_budget(repo: Path, monkeypatch) -> None:
    seen: dict[str, int | None] = {}
    real = repo_mod._git

    def spy(args, cwd, timeout=None):
        if args[:2] == ["worktree", "add"]:
            seen["timeout"] = timeout
        return real(args, cwd, timeout)

    monkeypatch.setattr(repo_mod, "_git", spy)
    result = runner.invoke(repo_app, ["new", "budgeted", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    assert seen["timeout"] == repo_mod._GIT_CHECKOUT_TIMEOUT
    assert repo_mod._GIT_CHECKOUT_TIMEOUT >= 120, "a 13.6k-file checkout measured 8s idle; 15s was the bug"


# ── new: a worktree with something running from it is not removed by accident ──


def test_remove_refuses_a_worktree_with_a_live_process_inside_it(repo: Path) -> None:
    """A committed, clean worktree passes git's dirty check — and a dev server was
    running from it. One session's `remove` took another session's servers and folder
    out from under a browser run (2026-09-20). Now the running process is named and
    the removal refused unless --force."""
    pytest.importorskip("psutil")
    wt = _worktree(repo, "served")
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=str(wt), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.3)
        live = repo_mod.live_processes_in(wt)
        assert any(p["pid"] == child.pid for p in live), live

        result = runner.invoke(repo_app, ["remove", "served", "--repo", str(repo)])
        assert result.exit_code == 1, result.output
        assert "running from it" in result.output and str(child.pid) in result.output
        assert wt.exists(), "the worktree must survive"
        assert any(w["path"].name == "served" for w in list_worktrees(repo))

        # --force is the explicit "I know": the folder may still be held on Windows
        # by the live child, so only the REGISTRATION is asserted gone here.
        result = runner.invoke(repo_app, ["remove", "served", "--repo", str(repo), "--force"])
        assert all(w["path"].name != "served" for w in list_worktrees(repo)), result.output
    finally:
        child.kill()
        child.wait(timeout=10)


def test_live_processes_in_skips_the_caller_itself(repo: Path) -> None:
    """The shell that runs `navig repo remove` is often parked inside the worktree —
    it must not count as a reason to refuse (that is what the leftover-folder hint
    is for)."""
    pytest.importorskip("psutil")
    wt = _worktree(repo, "parked")
    here = os.getcwd()
    os.chdir(wt)
    try:
        assert all(p["pid"] != os.getpid() for p in repo_mod.live_processes_in(wt))
    finally:
        os.chdir(here)
