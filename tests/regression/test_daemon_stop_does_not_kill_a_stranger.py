"""``navig service stop`` force-killed whatever PID the pidfile named, unverified.

A pidfile records a NUMBER, and a number is not an identity. ``NavigDaemon.read_pid()``
returned the recorded integer whenever the file parsed, so once the daemon had crashed or
the machine had rebooted — and the OS had handed that number to something else — every
consumer acted on a stranger:

  * ``stop_running_daemon`` issued ``taskkill /PID n /T`` and then ``/F /T`` with **no**
    identity check whatsoever. ``/T`` takes the whole process TREE, so an unrelated
    application and its children died.
  * ``is_running`` returned True because *something* answered to the number, so
    ``navig service status``, ``navig doctor`` and the tray all reported a daemon that
    was not there.
  * ``navig service start`` then refused to start ("already running").

The irony is local: ``_kill_orphan_daemons``, twenty lines below the stop path, is
meticulously scoped by ``config_dir_of`` and carries a docstring about the documented
catastrophe of killing the operator's brain — while the function that calls it killed the
pidfile's number blind.

``pid_from_pidfile`` is the canonical fix and was already adopted by ``navig agent stop``,
``gateway``, ``tray`` and the MCP agent tool. The supervisor was the holdout: harden one
path into a destructive action and you must enumerate EVERY path into it.

The recycled-PID condition is real, not simulated with mocks: a live process is named in
the pidfile, and the file's mtime is backdated to before that process existed — which is
exactly what a reboot leaves behind.
"""

from __future__ import annotations

import os

import psutil
import pytest

import navig.daemon.supervisor as sv
from navig.daemon.supervisor import NavigDaemon


@pytest.fixture
def pidfile(tmp_path, monkeypatch):
    """A pidfile naming THIS (live) process, honoured by ``_pid_file()``."""
    path = tmp_path / "supervisor.pid"
    path.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(sv, "PID_FILE", path)
    return path


def _make_look_recycled(path) -> None:
    """Backdate the pidfile to before this process started.

    The owner writes its pidfile just *after* starting, so ``create_time <= mtime`` holds
    for a genuine owner and can only be violated by a process that inherited the number.
    """
    started = psutil.Process(os.getpid()).create_time()
    stale = started - 3600
    os.utime(path, (stale, stale))


def test_a_genuine_daemon_is_still_reported_and_stoppable(pidfile) -> None:
    """Anti-vacuity, and the direction that matters most.

    Over-tightening this check would make a LIVE daemon unstoppable and invisible — a
    worse failure than the one being fixed (it is what happened to ``navig agent stop``).
    A pidfile written by its still-running owner must resolve to that PID.
    """
    assert NavigDaemon.read_pid() == os.getpid()
    assert NavigDaemon.is_running() is True


def test_a_recycled_pid_is_not_reported_as_the_running_daemon(pidfile) -> None:
    """The number is live, but it belongs to somebody else now."""
    _make_look_recycled(pidfile)

    assert NavigDaemon.read_pid() is None, (
        "read_pid trusted a pidfile whose process started after the file was written — "
        "that is a stranger, not the daemon"
    )
    assert NavigDaemon.is_running() is False, (
        "is_running reported a daemon because a recycled PID answered to the number"
    )


def test_stop_issues_no_kill_for_a_pid_it_cannot_identify(pidfile, monkeypatch) -> None:
    """THE REGRESSION: no process command may be issued for an unverified PID.

    Note what the pidfile names here — this very test process. On the pre-fix code the
    assertions below are not what stops the damage; the ``_forbidden`` stubs are, because
    ``stop_running_daemon`` would otherwise send ``taskkill /PID <pytest> /T`` to the
    runner itself. That is the bug, stated exactly.
    """
    _make_look_recycled(pidfile)

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            f"stop issued a process command for a PID it could not identify: {args!r}"
        )

    monkeypatch.setattr("subprocess.run", _forbidden)
    monkeypatch.setattr("os.kill", _forbidden)
    # The orphan sweep is separately scoped by config_dir_of and shells out to WMI/pgrep;
    # stub it so this test measures the pidfile path alone.
    swept: list[dict] = []
    monkeypatch.setattr(
        NavigDaemon,
        "_kill_orphan_daemons",
        staticmethod(lambda **kw: swept.append(kw) or []),
    )

    assert NavigDaemon.stop_running_daemon() is False, (
        "stop reported success for a daemon that was not running"
    )
    assert swept == [{}], (
        "the scoped orphan sweep should still run — it is the safe way to reap a stale "
        "generation, and it is what makes refusing the unverified kill costless"
    )
    assert pidfile.exists(), (
        "a pidfile that merely failed verification must not be deleted: verification can "
        "fail transiently (AccessDenied), and deleting a live daemon's pidfile would "
        "leave it unmanageable"
    )


def test_the_canonical_helper_is_what_reads_the_pidfile() -> None:
    """Pin the mechanism, not just the behaviour.

    A future edit that re-inlines ``int(path.read_text())`` would pass the recycled test
    only until someone "simplified" it back. The whole point is that there is ONE answer
    to "is this pidfile still its owner's", shared with agent/gateway/tray.
    """
    import ast
    import inspect
    import textwrap

    # getsource returns the method still indented inside its class, with its decorator.
    src = textwrap.dedent(inspect.getsource(NavigDaemon.read_pid))
    tree = ast.parse(src)
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "pid_from_pidfile" in called, (
        "read_pid no longer goes through pid_from_pidfile — the recycled-PID check is the "
        "only thing standing between `navig service stop` and an unrelated process tree"
    )
    assert "int" not in called, "read_pid parses the pidfile itself again — that is the bug"
