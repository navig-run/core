"""Regression tests for `navig update` making freshly-installed code LIVE.

The "merged but not live" gap: an editable/pip install loads its source into memory
once at boot, so a pull/upgrade changes disk but the running daemon keeps executing the
old code. `navig update` now reloads the daemon after a change-applying update. These
tests pin that behaviour — including the elevation gating — WITHOUT ever restarting the
operator's real daemon (every subprocess + daemon probe is mocked).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from navig.commands import update as U


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_daemon(
    monkeypatch,
    *,
    running: bool,
    pid: int = 4242,
    elevated_daemon: bool | None = None,
    we_elevated: bool = False,
) -> None:
    import navig.commands.service as svc
    import navig.daemon.supervisor as sup

    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: running), raising=False)
    monkeypatch.setattr(sup.NavigDaemon, "read_pid", staticmethod(lambda: pid), raising=False)
    monkeypatch.setattr(svc, "_is_elevated", lambda: we_elevated, raising=False)
    monkeypatch.setattr(svc, "_process_is_elevated", lambda _p: elevated_daemon, raising=False)


# --------------------------------------------------------------------------- reload step


def test_reload_skipped_when_daemon_not_running(monkeypatch):
    _patch_daemon(monkeypatch, running=False)
    called: list = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a) or _FakeCompleted())
    res = U._step_reload_daemon(interactive=True)
    assert res.ok is True
    assert "not running" in res.note
    assert not called  # never shelled out to restart nothing


def test_reload_restarts_normal_daemon_without_admin(monkeypatch):
    _patch_daemon(monkeypatch, running=True, elevated_daemon=False, we_elevated=False)
    cmds: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, *a, **k: cmds.append(cmd) or _FakeCompleted(returncode=0)
    )
    res = U._step_reload_daemon(interactive=True)
    assert res.ok is True
    assert cmds and cmds[0][-2:] == ["service", "restart"]
    assert "--admin" not in cmds[0]


def test_reload_elevated_interactive_uses_admin(monkeypatch):
    _patch_daemon(monkeypatch, running=True, elevated_daemon=True, we_elevated=False)
    cmds: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, *a, **k: cmds.append(cmd) or _FakeCompleted(returncode=0)
    )
    res = U._step_reload_daemon(interactive=True)
    assert res.ok is True
    assert "--admin" in cmds[0]


def test_reload_elevated_noninteractive_refuses_and_hints(monkeypatch):
    """A non-interactive `navig update` must NOT pop a blocking UAC dialog — it reports the
    exact command instead."""
    _patch_daemon(monkeypatch, running=True, elevated_daemon=True, we_elevated=False)
    cmds: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, *a, **k: cmds.append(cmd) or _FakeCompleted(returncode=0)
    )
    res = U._step_reload_daemon(interactive=False)
    assert res.ok is False
    assert "service restart --admin" in res.note
    assert not cmds  # never launched a UAC prompt


def test_reload_reports_restart_failure(monkeypatch):
    _patch_daemon(monkeypatch, running=True, elevated_daemon=False, we_elevated=False)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeCompleted(returncode=1, stderr="boom")
    )
    res = U._step_reload_daemon(interactive=True)
    assert res.ok is False
    assert "boom" in res.note


# ------------------------------------------------------------------- git "did it change?"


def test_step_git_reports_commit_delta_and_marks_changed(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    seq = {"n": 0}

    def _run(cmd, *a, **k):
        if "rev-parse" in cmd:
            seq["n"] += 1
            return _FakeCompleted(0, stdout=("aaaaaaaa\n" if seq["n"] == 1 else "bbbbbbbb\n"))
        if "pull" in cmd:
            return _FakeCompleted(0, stdout="Updating a..b\nFast-forward\n")
        if "rev-list" in cmd:
            return _FakeCompleted(0, stdout="8\n")
        return _FakeCompleted(0)  # the editable rebuild

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(U, "_find_uv", lambda: None)  # force the pip rebuild path
    res = U._step_git(tmp_path, force=False)
    assert res.ok is True
    assert res.changed is True
    assert "+8 commits" in res.note


def test_step_git_no_change_is_not_marked_changed(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()

    def _run(cmd, *a, **k):
        if "rev-parse" in cmd:
            return _FakeCompleted(0, stdout="samesha\n")  # before == after
        if "pull" in cmd:
            return _FakeCompleted(0, stdout="Already up to date.\n")
        return _FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", _run)
    res = U._step_git(tmp_path, force=False)
    assert res.ok is True
    assert res.changed is False
    assert "already on latest" in res.note


# ------------------------------------------------------- git-checkout detection (monorepo)


def test_is_navig_git_checkout_uses_ls_files_not_dotgit(monkeypatch):
    """`.git` is at the repo ROOT, not core/ — so detection must ask git whether the source
    is TRACKED (`ls-files --error-unmatch`), never `(src/.git).exists()`."""
    cmds: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, *a, **k: cmds.append(cmd) or _FakeCompleted(0)
    )
    assert U._is_navig_git_checkout(Path("/anywhere")) is True
    assert "ls-files" in cmds[0] and "--error-unmatch" in cmds[0], cmds[0]


def test_is_navig_git_checkout_false_when_source_not_tracked(monkeypatch):
    """A wheel in site-packages (even inside an unrelated repo) → navig source NOT tracked."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(returncode=1))
    assert U._is_navig_git_checkout(Path("/anywhere")) is False


def test_is_navig_git_checkout_true_on_the_real_source_tree():
    """Regression guard for the monorepo `.git`-at-root trap: the suite runs from the repo,
    where navig's source IS tracked. The old `(src/.git).exists()` returned False here — which
    made `navig update` silently take the pip path and never pull."""
    import navig

    src = Path(navig.__file__).resolve().parent.parent  # <src>/navig/__init__.py -> <src>
    assert U._is_navig_git_checkout(src) is True


# ------------------------------------------------- _run_update wiring (git path + freshness)


def _patch_update_steps(monkeypatch, calls):
    """Mock every side-effecting step of _run_update so it never touches git/daemon/network,
    and record which path was taken."""
    monkeypatch.setattr(U, "_is_navig_git_checkout", lambda _src: True)  # force the git path
    monkeypatch.setattr(
        U, "_step_git",
        lambda src, force: calls.__setitem__("git", calls["git"] + 1)
        or U._Result("Sync with upstream", ok=True, note="+2 commits → abc1234", changed=True),
    )
    monkeypatch.setattr(
        U, "_step_pypi",
        lambda force: calls.__setitem__("pypi", calls["pypi"] + 1) or U._Result("pypi", ok=True),
    )
    monkeypatch.setattr(U, "_reload_version", lambda: "3.24.0")
    monkeypatch.setattr(U, "_step_plugins", lambda: U._Result("Plugins", ok=True))
    monkeypatch.setattr(
        U, "_step_reload_daemon",
        lambda interactive: calls.__setitem__("reload", calls["reload"] + 1)
        or U._Result("Reload daemon (live code)", ok=True, note="restarted"),
    )
    monkeypatch.setattr(
        U, "_step_doctor",
        lambda skip_sections=frozenset(): calls.__setitem__("doctor_skip", set(skip_sections))
        or U._Result("Config doctor", ok=True),
    )
    monkeypatch.setattr(U, "_sync_path", lambda *a, **k: None)
    monkeypatch.setattr(U, "_offer_redeploys", lambda *a, **k: None)


def test_run_update_takes_git_path_and_skips_daemon_freshness_when_reloading(monkeypatch):
    calls = {"git": 0, "pypi": 0, "reload": 0, "doctor_skip": None}
    _patch_update_steps(monkeypatch, calls)

    U._run_update(force=True, restart=True)

    assert calls["git"] == 1 and calls["pypi"] == 0, "must take the GIT path, not pip"
    assert calls["reload"] == 1, "a change-applying git pull must reload the daemon"
    assert calls["doctor_skip"] == {"Daemon"}, "freshness must be skipped while about to reload"


def test_run_update_keeps_daemon_freshness_under_no_restart(monkeypatch):
    calls = {"git": 0, "pypi": 0, "reload": 0, "doctor_skip": None}
    _patch_update_steps(monkeypatch, calls)

    U._run_update(force=True, restart=False)

    assert calls["reload"] == 0, "--no-restart must NOT reload the daemon"
    assert calls["doctor_skip"] == set(), "under --no-restart a genuinely stale daemon is kept (honest)"
